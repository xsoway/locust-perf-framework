#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time        : 2026/6/25 20:28
# @Project     : test-locust-platform
# @Filename    : debug.py
# @Author      : Alan_Hsu
# @Description :
#!/usr/bin/env python
# -*- coding: utf-8 -*-


from collections import deque

from threading import Lock

from locust import HttpUser, task

from locust.exception import StopUser
URLS = [
    "/dealer/public/26/20260528/carimage/image/32c8531fb7b9e86769f37665f037ff8a-a.webp",
    "/dealer/public/26/20260528/carimage/image/0321decaf772ee15e1633e76057918c5-a.webp",
    "/dealer/public/26/20260528/carimage/image/65ac9f0459b44d640b2a7642c16e39bb-a.webp",
    "/dealer/public/26/20260528/carimage/image/f13ed7c1c9f7903133d192723860c739-a.webp",
    "/dealer/public/26/20260528/carimage/image/07ab067855bf69198063a99147fab55b-a.webp",
    "/dealer/public/26/20260527/carimage/image/c57dceccf77801fb4b9786f2fb9bf820-a.webp",
    "/dealer/public/26/20260527/carimage/image/c4f2354857443fd92ce29c94d75528df-a.webp",
    "/dealer/public/26/20260527/carimage/image/180b7ea84fb55b488995dc6defe29801-a.webp",
    "/dealer/public/26/20260526/carimage/image/be01b7718bb718d251681e912b32af87-a.webp",
    "/dealer/public/26/20260526/carimage/image/fd3fb200cd27523ad90a8fcc5e671811-a.webp",
    "/dealer/public/26/20260526/carimage/image/9a4a89e6b533ad665e4ea7618c2f718c-a.webp",
    "/dealer/public/26/20260526/carimage/image/2ca147b055a0e453ba51b656d8bfac00-a.webp",
    "/dealer/public/26/20260526/carimage/image/f355ea9aa92bec68fbdb8bc18f4f9c83-a.webp",
    "/dealer/public/26/20260526/carimage/image/e15a4247dd403ce50bc1ddb19ab1951d-a.webp",
    "/dealer/public/26/20260526/carimage/image/3055759fc3c510bad7dabaf16b6b536c-a.webp",
    "/dealer/public/26/20260526/carimage/image/ea8d784adc103d1ec5df1e64530b7ff9-a.webp",
    "/dealer/public/26/20260526/carimage/image/722318f0b1c58ab78006761826d3b96b-a.webp",
    "/dealer/public/26/20260526/carimage/image/b94b8819e9ae9f16723ee6f39879db63-a.webp",
    "/dealer/public/26/20260526/carimage/image/4f8c89871a228108d2182ab7722a2d02-a.webp",
    "/dealer/public/26/20260526/carimage/image/a62e75b2078fb9f7fec6f21db081cdee-a.webp",
]

#
# class ImageUser(HttpUser):
#     host = "https://dealercosc.taocheche.com.cn"
#
#     # 不等待，持续压测
#     wait_time = constant(0)
#
#     def on_start(self):
#         # 每个虚拟用户都有自己的循环迭代器
#         self.url_cycle = cycle(URLS)
#
#     @task
#     def get_image(self):
#         url = next(self.url_cycle)
#
#         with self.client.get(
#             url,
#             name="GET Image",
#             catch_response=True,
#             headers={
#                 "User-Agent": (
#                     "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
#                     "AppleWebKit/537.36 (KHTML, like Gecko) "
#                     "Chrome/126.0.0.0 Safari/537.36"
#                 ),
#                 "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
#                 "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
#             },
#         ) as response:
#
#             if response.status_code != 200:
#                 response.failure(f"HTTP {response.status_code}")
#                 return
#
#             content_type = response.headers.get("Content-Type", "")
#             if not content_type.startswith("image/"):
#                 response.failure(f"Content-Type错误：{content_type}")
#                 return
#
#             response.success()


url_queue = deque(URLS)

lock = Lock()

class ImageUser(HttpUser):

    host = "https://dealercosc.taocheche.com.cn"

    def get_next_url(self):

        with lock:

            if url_queue:

                return url_queue.popleft()

            return None

    @task

    def get_image(self):

        url = self.get_next_url()

        if url is None:

            raise StopUser()

        with self.client.get(

            url,

            catch_response=True,

            name="GET Image",

        ) as response:

            if response.status_code == 200:

                response.success()

            else:

                response.failure(f"HTTP {response.status_code}")

            # 当前用户完成任务后立即退出

            raise StopUser()