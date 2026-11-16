const api = {
  async request(method, path, body) {
    const response = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!response.ok) {
      let detail = response.statusText;
      try {
        const data = await response.json();
        detail = data.detail || detail;
      } catch (_) {
        /* 非 JSON 错误体，保留状态文本 */
      }
      throw new Error(detail);
    }
    return response.json();
  },
  get: (path) => api.request("GET", path),
  post: (path, body) => api.request("POST", path, body),
  del: (path) => api.request("DELETE", path),
};

const MODE_LABELS = { mention: "点名", free: "自由讨论", supervisor: "主持人调度" };

const state = {
  me: null,
  conversations: [],
  workspaceMembers: [],
  detail: null,
  currentId: null,
  mode: "mention",
  modes: JSON.parse(localStorage.getItem("aidiscussion:modes") || "{}"),
  ws: null,
  streams: new Map(),
};

const el = {
  conversations: document.getElementById("conversationList"),
  contacts: document.getElementById("contactList"),
  messages: document.getElementById("messages"),
  title: document.getElementById("chatTitle"),
  meta: document.getElementById("chatMeta"),
  modeSwitch: document.getElementById("modeSwitch"),
  membersBtn: document.getElementById("membersBtn"),
  composer: document.getElementById("composer"),
  input: document.getElementById("input"),
  conn: document.getElementById("conn"),
  toast: document.getElementById("toast"),
  membersModal: document.getElementById("membersModal"),
  memberList: document.getElementById("memberList"),
  addMemberBlock: document.getElementById("addMemberBlock"),
  addMemberSelect: document.getElementById("addMemberSelect"),
  newGroupModal: document.getElementById("newGroupModal"),
  groupTitle: document.getElementById("groupTitle"),
  groupMemberOptions: document.getElementById("groupMemberOptions"),
};

/* ---------------- 基础工具 ---------------- */

function avatarFor(name, extra = "") {
  const node = document.createElement("div");
  node.className = "avatar " + extra;
  node.textContent = (name || "?").trim().charAt(0) || "?";
  let hash = 0;
  for (const char of name || "") hash = (hash * 31 + char.codePointAt(0)) % 360;
  node.style.background = `linear-gradient(135deg, hsl(${hash} 62% 52%), hsl(${(hash + 34) % 360} 64% 44%))`;
  return node;
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  return sameDay
    ? date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    : `${date.getMonth() + 1}/${date.getDate()}`;
}

let toastTimer = null;
function toast(message, isError = false) {
  el.toast.textContent = message;
  el.toast.classList.toggle("error", isError);
  el.toast.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.toast.classList.add("hidden"), 2600);
}

function currentConversation() {
  return state.conversations.find((item) => item.id === state.currentId) || state.detail;
}

/* ---------------- 启动 ---------------- */

async function init() {
  bindEvents();
  try {
    const [me, conversations, members] = await Promise.all([
      api.get("/api/me"),
      api.get("/api/conversations"),
      api.get("/api/members"),
    ]);
    state.me = me;
    state.conversations = conversations;
    state.workspaceMembers = members;

    renderConversations();
    renderContacts();

    const remembered = localStorage.getItem("aidiscussion:last");
    const target =
      state.conversations.find((item) => item.id === remembered) || state.conversations[0];
    if (target) await selectConversation(target.id);
  } catch (error) {
    toast(`初始化失败：${error.message}`, true);
  }
}

/* ---------------- 渲染 ---------------- */

function renderConversations() {
  el.conversations.textContent = "";
  for (const conversation of state.conversations) {
    const item = document.createElement("li");
    item.className = "list-item" + (conversation.id === state.currentId ? " active" : "");

    const names = conversation.members.map((member) => member.name);
    const label =
      conversation.type === "group"
        ? conversation.title
        : names.find((name) => name !== state.me?.name) || conversation.title;
    item.append(avatarFor(label));

    const main = document.createElement("div");
    main.className = "list-item-main";
    const title = document.createElement("strong");
    title.textContent = label;
    const preview = document.createElement("span");
    preview.textContent = conversation.last_message
      ? `${conversation.last_message.sender_name}：${conversation.last_message.content}`
      : "还没有消息";
    main.append(title, preview);
    item.append(main);

    const time = document.createElement("time");
    time.textContent = formatTime(conversation.last_message_at);
    item.append(time);

    item.addEventListener("click", () => selectConversation(conversation.id));
    el.conversations.append(item);
  }
}

function renderContacts() {
  el.contacts.textContent = "";
  const ais = state.workspaceMembers.filter((member) => member.kind === "ai");
  for (const member of ais) {
    const item = document.createElement("li");
    item.className = "list-item";
    item.append(avatarFor(member.name));
    const main = document.createElement("div");
    main.className = "list-item-main";
    const name = document.createElement("strong");
    name.textContent = member.name;
    const hint = document.createElement("span");
    hint.textContent = "点击私聊";
    main.append(name, hint);
    item.append(main);
    item.addEventListener("click", () => openDirect(member.id));
    el.contacts.append(item);
  }
}

function renderMessages(messages) {
  el.messages.textContent = "";
  if (!messages.length) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    const mark = document.createElement("div");
    mark.className = "empty-mark";
    mark.textContent = "A";
    const title = document.createElement("h2");
    title.textContent = "还没有消息";
    const hint = document.createElement("p");
    hint.textContent = "说点什么，群里的 AI 会依次回应；输入 @名字 可以点名。";
    empty.append(mark, title, hint);
    el.messages.append(empty);
    return;
  }
  for (const message of messages) {
    appendMessage(message);
  }
  el.messages.scrollTop = el.messages.scrollHeight;
}

function messageNode(message, { streaming = false } = {}) {
  const self = message.sender_id === state.me?.id;
  const wrap = document.createElement("div");
  wrap.className = "msg" + (self ? " self" : "");

  const avatar = avatarFor(message.sender_name, "lg");
  const body = document.createElement("div");
  body.className = "msg-body";

  const name = document.createElement("div");
  name.className = "msg-name";
  name.textContent = `${message.sender_name} · ${formatTime(message.created_at || new Date())}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble" + (streaming ? " caret" : "");
  bubble.textContent = message.content || "";

  body.append(name, bubble);
  wrap.append(avatar, body);
  return { wrap, body, bubble };
}

function appendMessage(message) {
  const { wrap, bubble } = messageNode(message);
  el.messages.append(wrap);
  return bubble;
}

function renderHeader() {
  const conversation = state.detail;
  if (!conversation) return;

  el.title.textContent = conversation.title;
  const isGroup = conversation.type === "group";
  const names = conversation.members.map((member) => member.name).join("、");
  el.meta.textContent = isGroup
    ? `${conversation.members.length} 名成员 · ${MODE_LABELS[state.mode]}`
    : "私聊";

  el.modeSwitch.hidden = !isGroup;
  el.membersBtn.hidden = false;
  el.composer.hidden = false;
  el.membersBtn.title = names;

  for (const button of el.modeSwitch.querySelectorAll("button")) {
    button.classList.toggle("active", button.dataset.mode === state.mode);
  }
  el.input.placeholder = isGroup
    ? "@ 某个成员可指定回答，Enter 发送"
    : "输入消息，Enter 发送，Shift + Enter 换行";
}

function updatePreview(message) {
  const conversation = state.conversations.find((item) => item.id === state.currentId);
  if (!conversation) return;
  conversation.last_message = message;
  conversation.last_message_at = message.created_at;
  state.conversations.sort(
    (a, b) => new Date(b.last_message_at || 0) - new Date(a.last_message_at || 0)
  );
  renderConversations();
}

/* ---------------- 会话 ---------------- */

async function selectConversation(id) {
  state.currentId = id;
  localStorage.setItem("aidiscussion:last", id);
  closeSocket();
  state.streams.clear();

  try {
    const [detail, messages] = await Promise.all([
      api.get(`/api/conversations/${id}`),
      api.get(`/api/conversations/${id}/messages?limit=100`),
    ]);
    state.detail = detail;
    state.mode = state.modes[id] || "mention";
    renderConversations();
    renderHeader();
    renderMessages(messages);
    connectSocket(id);
  } catch (error) {
    toast(`打开会话失败：${error.message}`, true);
  }
}

async function reloadCurrent({ keepMessages = true } = {}) {
  if (!state.currentId) return;
  const detail = await api.get(`/api/conversations/${state.currentId}`);
  state.detail = detail;
  if (!keepMessages) {
    const messages = await api.get(`/api/conversations/${state.currentId}/messages?limit=100`);
    renderMessages(messages);
  }
  renderHeader();
  renderConversations();
}

async function refreshWorkspace() {
  state.workspaceMembers = await api.get("/api/members");
  renderContacts();
}

async function openDirect(memberId) {
  try {
    const conversation = await api.post("/api/conversations/direct", { member_id: memberId });
    state.conversations = await api.get("/api/conversations");
    renderConversations();
    await selectConversation(conversation.id);
    hideModal("membersModal");
  } catch (error) {
    toast(error.message, true);
  }
}

/* ---------------- WebSocket ---------------- */

function setConnection(on) {
  el.conn.classList.toggle("on", on);
  el.conn.title = on ? "已连接" : "连接断开";
}

function closeSocket() {
  if (state.ws) {
    const socket = state.ws;
    state.ws = null;
    socket.onclose = null;
    socket.close();
  }
  setConnection(false);
}

function connectSocket(conversationId) {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/ws/conversations/${conversationId}`);
  state.ws = socket;

  socket.onopen = () => setConnection(true);
  socket.onclose = () => {
    setConnection(false);
    if (state.currentId === conversationId) {
      setTimeout(() => {
        if (state.currentId === conversationId && !state.ws) connectSocket(conversationId);
      }, 1500);
    }
  };
  socket.onerror = () => setConnection(false);
  socket.onmessage = (event) => {
    try {
      handleEvent(JSON.parse(event.data));
    } catch (error) {
      console.error("无法解析服务端消息", error);
    }
  };
}

function scrollIfNearBottom(force = false) {
  const distance = el.messages.scrollHeight - el.messages.scrollTop - el.messages.clientHeight;
  if (force || distance < 140) {
    el.messages.scrollTop = el.messages.scrollHeight;
  }
}

function handleEvent(event) {
  switch (event.type) {
    case "user_message": {
      appendMessage(event.message);
      updatePreview(event.message);
      scrollIfNearBottom(true);
      break;
    }
    case "reply_start": {
      const { wrap, body, bubble } = messageNode(
        { sender_name: event.member.name, content: "" },
        { streaming: true }
      );
      el.messages.append(wrap);
      state.streams.set(event.member.id, { wrap, body, bubble, name: event.member.name });
      scrollIfNearBottom();
      break;
    }
    case "reply_delta": {
      const stream = state.streams.get(event.member_id);
      if (!stream) break;
      stream.bubble.textContent += event.text;
      scrollIfNearBottom();
      break;
    }
    case "reply_done": {
      const stream = state.streams.get(event.message.sender_id);
      if (stream) {
        stream.bubble.textContent = event.message.content;
        stream.bubble.classList.remove("caret");
        state.streams.delete(event.message.sender_id);
      } else {
        appendMessage(event.message);
      }
      updatePreview(event.message);
      scrollIfNearBottom();
      break;
    }
    case "reply_failed": {
      const stream = state.streams.get(event.member_id);
      if (stream) {
        stream.bubble.classList.remove("caret");
        stream.bubble.classList.add("failed");
        const note = document.createElement("div");
        note.className = "msg-note";
        note.textContent = `生成中断：${event.error}`;
        stream.body.append(note);
        state.streams.delete(event.member_id);
      } else {
        toast(event.error, true);
      }
      break;
    }
    case "error":
      toast(event.message, true);
      break;
    default:
      break;
  }
}

/* ---------------- 发送 ---------------- */

function resizeInput() {
  el.input.style.height = "auto";
  el.input.style.height = `${Math.min(el.input.scrollHeight, 160)}px`;
}

function sendMessage() {
  const content = el.input.value.trim();
  if (!content) return;
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
    toast("连接尚未就绪，稍等一秒再发送", true);
    return;
  }
  state.ws.send(JSON.stringify({ type: "send", content, mode: state.mode }));
  el.input.value = "";
  resizeInput();
  el.input.focus();
}

/* ---------------- 成员管理 ---------------- */

function showModal(id) {
  document.getElementById(id).classList.remove("hidden");
}

function hideModal(id) {
  document.getElementById(id).classList.add("hidden");
}

async function openMembers() {
  if (!state.detail) return;
  try {
    await refreshWorkspace();
    renderMemberModal();
    showModal("membersModal");
  } catch (error) {
    toast(error.message, true);
  }
}

function renderMemberModal() {
  const conversation = state.detail;
  if (!conversation) return;
  const isGroup = conversation.type === "group";
  const currentIds = new Set(conversation.members.map((member) => member.id));

  el.memberList.textContent = "";
  for (const member of conversation.members) {
    const item = document.createElement("li");
    item.append(avatarFor(member.name));

    const meta = document.createElement("div");
    meta.className = "meta";
    const name = document.createElement("strong");
    name.textContent = member.name + (member.id === state.me?.id ? "（我）" : "");
    const persona = document.createElement("span");
    persona.textContent = member.system_prompt || "普通成员";
    meta.append(name, persona);
    item.append(meta);

    if (isGroup && member.kind === "ai") {
      const remove = document.createElement("button");
      remove.className = "danger-btn spacer";
      remove.textContent = "移出";
      remove.addEventListener("click", () => removeMember(member));
      item.append(remove);
    }
    el.memberList.append(item);
  }

  el.addMemberBlock.hidden = !isGroup;
  el.addMemberSelect.textContent = "";
  const candidates = state.workspaceMembers.filter(
    (member) => member.kind === "ai" && !currentIds.has(member.id)
  );
  for (const member of candidates) {
    const option = document.createElement("option");
    option.value = member.id;
    option.textContent = member.name;
    el.addMemberSelect.append(option);
  }
  if (!candidates.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "没有可添加的 AI，先新建一个";
    el.addMemberSelect.append(option);
  }
}

async function removeMember(member) {
  try {
    await api.del(`/api/conversations/${state.currentId}/members/${member.id}`);
    await reloadCurrent({ keepMessages: false });
    await refreshWorkspace();
    renderMemberModal();
    toast(`已把「${member.name}」移出群`);
  } catch (error) {
    toast(error.message, true);
  }
}

async function addMember() {
  const memberId = el.addMemberSelect.value;
  if (!memberId) return;
  try {
    await api.post(`/api/conversations/${state.currentId}/members`, { member_id: memberId });
    await reloadCurrent({ keepMessages: false });
    renderMemberModal();
    toast("已加入群聊");
  } catch (error) {
    toast(error.message, true);
  }
}

async function createMember() {
  const name = document.getElementById("newMemberName").value.trim();
  const persona = document.getElementById("newMemberPersona").value.trim();
  const model = document.getElementById("newMemberModel").value.trim();
  if (!name) {
    toast("先给成员起个名字", true);
    return;
  }
  try {
    const member = await api.post("/api/members", {
      name,
      system_prompt: persona,
      model: model || null,
    });
    if (state.detail?.type === "group") {
      await api.post(`/api/conversations/${state.currentId}/members`, {
        member_id: member.id,
      });
    }
    document.getElementById("newMemberName").value = "";
    document.getElementById("newMemberPersona").value = "";
    document.getElementById("newMemberModel").value = "";
    await refreshWorkspace();
    await reloadCurrent({ keepMessages: false });
    renderMemberModal();
    toast(`已创建「${member.name}」`);
  } catch (error) {
    toast(error.message, true);
  }
}

/* ---------------- 新建群聊 ---------------- */

function openNewGroup() {
  el.groupTitle.value = "";
  el.groupMemberOptions.textContent = "";
  const ais = state.workspaceMembers.filter((member) => member.kind === "ai");
  if (!ais.length) {
    const hint = document.createElement("li");
    hint.textContent = "目前还没有 AI 成员，先在成员管理里创建";
    el.groupMemberOptions.append(hint);
  }
  for (const member of ais) {
    const item = document.createElement("li");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = member.id;
    checkbox.checked = true;
    const label = document.createElement("span");
    label.textContent = member.name;
    item.append(checkbox, label);
    el.groupMemberOptions.append(item);
  }
  showModal("newGroupModal");
}

async function createGroup() {
  const title = el.groupTitle.value.trim() || "新群聊";
  const memberIds = [...el.groupMemberOptions.querySelectorAll("input:checked")].map(
    (input) => input.value
  );
  try {
    const conversation = await api.post("/api/conversations", {
      title,
      member_ids: memberIds,
    });
    state.conversations = await api.get("/api/conversations");
    renderConversations();
    hideModal("newGroupModal");
    await selectConversation(conversation.id);
    toast(`已创建群「${conversation.title}」`);
  } catch (error) {
    toast(error.message, true);
  }
}

/* ---------------- 事件绑定 ---------------- */

function setMode(mode) {
  state.mode = mode;
  if (state.currentId) {
    state.modes[state.currentId] = mode;
    localStorage.setItem("aidiscussion:modes", JSON.stringify(state.modes));
  }
  renderHeader();
  el.input.focus();
}

function bindEvents() {
  el.composer.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage();
  });

  el.input.addEventListener("input", resizeInput);
  el.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });

  for (const button of el.modeSwitch.querySelectorAll("button")) {
    button.addEventListener("click", () => setMode(button.dataset.mode));
  }

  document.getElementById("membersBtn").addEventListener("click", openMembers);
  document.getElementById("manageMembersBtn").addEventListener("click", openMembers);
  document.getElementById("addMemberBtn").addEventListener("click", addMember);
  document.getElementById("createMemberBtn").addEventListener("click", createMember);
  document.getElementById("newGroupBtn").addEventListener("click", openNewGroup);
  document.getElementById("createGroupBtn").addEventListener("click", createGroup);

  for (const button of document.querySelectorAll("[data-close]")) {
    button.addEventListener("click", () => hideModal(button.dataset.close));
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      hideModal("membersModal");
      hideModal("newGroupModal");
    }
  });
}

init();
