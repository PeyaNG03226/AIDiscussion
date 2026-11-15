uv.lock:
uv 根据 pyproject.toml 里你的愿望，去网上找包，把：
你要的包 + 它所有连带安装的子依赖，**每一个包精确版本、校验哈希全部记下来**，存进 uv.lock。

pyproject.toml:
requirements.txt是老方案, 这个是官方推荐的新标准
requirements.txt缺点:
- 只有依赖列表, 没有项目别的信息
- 不会自动生成锁文件, 想锁版本还得单独跑`pip freeze > requirements.txt`,会把虚拟环境里所有包(包括间接依赖)全导进去, 很乱