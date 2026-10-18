"""端到端验证：真实领取器 solve_captcha_page + 百炼 qwen3-vl-plus 打本地测试验证码页。"""
import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, "/workspace")
os.environ.setdefault("HEADLESS", "true")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from core.browser import launch_browser
from core.claimers.webapp import solve_captcha_page
from core.vision.solver import CaptchaSolver

URL = "http://localhost:8898/?n=12"
KEY = os.environ["BL_KEY"]
BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3-vl-plus"
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 3


async def main():
    solver = CaptchaSolver(api_key=KEY, base_url=BASE, model=MODEL)
    browser, closer = await launch_browser()
    oks = 0
    try:
        for i in range(ROUNDS):
            timings = {}
            t0 = time.time()
            ok = await solve_captcha_page(
                URL, solver, browser=browser,
                model=MODEL, tag=f"r{i+1}", timings=timings, debug=True,
                api_key=KEY, base_url=BASE)
            total = time.time() - t0
            ready = timings.get("ready", 0) - t0 if timings.get("ready") else -1
            submit = timings.get("submit", 0) - t0 if timings.get("submit") else -1
            print(f"第{i+1}轮: {'✅通过' if ok else '❌失败'} "
                  f"页面就绪+截图 {ready:.2f}s | 到点完提交 {submit:.2f}s | 总耗时 {total:.2f}s")
            oks += ok
    finally:
        await closer()
    print(f"\n结果：{oks}/{ROUNDS} 通过")


asyncio.run(main())
