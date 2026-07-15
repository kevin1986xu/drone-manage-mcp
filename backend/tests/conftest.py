"""单测一律跑 mock 数据源：先于 app 导入清掉真实数据源配置。

（app.config 的 load_dotenv 不覆盖已存在的环境变量，故此处置空即可屏蔽 .env）
"""

import os

os.environ["DRONE_API_BASE"] = ""
os.environ["AGENT_MODE"] = "scripted"
os.environ["WEATHER_PROVIDER"] = "mock"  # 单测不出网
