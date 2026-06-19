import base64
import json
from datetime import date
import requests

API_BASE = "https://swxy.haiqikeji.com/api/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
    "Content-Type": "application/json",
    "Origin": "https://swxy.haiqikeji.com",
}

# 学校ID
SCHOOL_ID = 15

# 账号列表（支持多个账号同时刷课）
ACCOUNTS = [
    {
        "number": "",
        "password": "",
        "course_ids": [1012365,1012305],
    },
    # {
    #     "number": "",
    #     "password": "",
    #     "course_ids": [1012366],
    # },
]

# 线程数（每个课程的并发线程数）
MAX_WORKERS = 15


def login(number, password):
    resp = requests.get(
        f"{API_BASE}user/login",
        params={"number": number, "password": password, "schoolId": SCHOOL_ID}
    )
    data = resp.json()
    if data.get("code") != 200:
        raise Exception(f"登录失败: {data.get('msg')}")
    token = data["data"]
    import re
    raw = base64.b64decode(token.split('.')[1] + '==').decode('latin-1')
    user_id = int(re.search(r'\\?"id\\?"\s*:\s*(\d+)', raw).group(1))
    return token, user_id


def fetch_course_ids(token, user_id):
    headers = {**HEADERS, "Authorization": token}
    resp = requests.get(
        f"{API_BASE}user/yee_my_course_list",
        params={"schoolId": SCHOOL_ID, "studentId": user_id, "type": 0, "pageNum": 1, "pageSize": 100},
        headers=headers
    )
    data = resp.json()
    if data.get("code") != 200:
        raise Exception(f"获取课程列表失败: {data.get('msg')}")
    today = date.today().isoformat()
    ids = [c["id"] for c in data["data"] if c.get("endDate", "") >= today]
    return ids