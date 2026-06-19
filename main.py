# ============================================================
# Author：X_ni_dada
# GitHub：https://github.com/Xnidada/haiqikeji
# 注意事项：本脚本仅供学习和技术研究使用，请遵守相关平台的使用规定。
# ============================================================
import requests
import time
import re
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import API_BASE, HEADERS, SCHOOL_ID, MAX_WORKERS, ACCOUNTS, login, fetch_course_ids


def make_headers(token):
    return {**HEADERS, "Authorization": token}


# ================== 工具函数 ==================
def parse_duration(duration_str: str) -> int:
    if not duration_str or duration_str == "0秒":
        return 0
    total_seconds = 0
    matches = re.findall(r'(\d+)([小时分秒])', duration_str)
    for num, unit in matches:
        num = int(num)
        if unit == '小时':
            total_seconds += num * 3600
        elif unit == '分':
            total_seconds += num * 60
        elif unit == '秒':
            total_seconds += num
    return total_seconds


def parse_duration_v2(duration_str: str) -> int:
    if not duration_str or duration_str == "0秒":
        return 0
    total_seconds = 0
    h_match = re.search(r'(\d+)\s*小时', duration_str)
    if h_match:
        total_seconds += int(h_match.group(1)) * 3600
    m_match = re.search(r'(\d+)\s*分', duration_str)
    if m_match:
        total_seconds += int(m_match.group(1)) * 60
    s_match = re.search(r'(\d+)\s*秒', duration_str)
    if s_match:
        total_seconds += int(s_match.group(1))
    return total_seconds


def get_duration(duration_str: str) -> int:
    result = parse_duration_v2(duration_str)
    if result > 0:
        return result
    result = parse_duration(duration_str)
    if result > 0:
        print(f"   ⚠️ 备用方法解析失败，原方法解析成功: {duration_str} -> {result}秒")
    return result


# ================== 获取学习进度 ==================
def get_study_progress(token, user_id, course_id):
    url = f"{API_BASE}user/get_study_progress"
    params = {
        "schoolId": SCHOOL_ID,
        "userId": user_id,
        "courseId": course_id
    }
    resp = requests.get(url, params=params, headers=make_headers(token))

    if resp.status_code == 200:
        data = resp.json()
        if data.get("code") == 200:
            node_list = data["data"]["nodeProgressList"]
            incomplete_nodes = [
                node for node in node_list
                if node["statusText"] != "已完成"
            ]
            print(f"[+] 共找到 {len(incomplete_nodes)} 个未完成视频")
            return incomplete_nodes
        else:
            print(f"[!] 获取进度失败: {data.get('msg')}")
    else:
        print(f"[!] HTTP 请求失败: {resp.status_code}")
    return []


# ================== API ==================
def create_session(token):
    sess = requests.Session()
    sess.headers.update(make_headers(token))
    return sess


def study_session_start(sess, user_id, node_id, course_id):
    url = f"{API_BASE}user/study_session_start"
    payload = {
        "schoolId": SCHOOL_ID,
        "userId": user_id,
        "courseId": course_id,
        "nodeId": node_id,
        "terminal": "web"
    }
    resp = sess.post(url, json=payload)
    if resp.status_code == 200 and resp.json().get("code") == 200:
        return resp.json().get("data")
    print("❌ 会话启动失败")
    return None


def study_session_heartbeat(sess, session_id):
    url = f"{API_BASE}user/study_session_heartbeat"
    payload = {"sessionId": session_id}
    resp = sess.post(url, json=payload)
    if resp.status_code == 200:
        try:
            data = resp.json()
            if data.get("code") == 200:
                return True
        except:
            pass
    return False


def study_session_end(sess, session_id):
    url = f"{API_BASE}user/study_session_end"
    payload = {"sessionId": session_id}
    sess.post(url, json=payload)


# ================== 核心逻辑 ==================
def simulate_for_node(token, number, user_id, node, course_id):
    try:
        node_id = node["nodeId"]
        video_name = node["nodeName"]
        video_duration = get_duration(node["videoDuration"])
        watched_duration = get_duration(node["watchDuration"])
        remaining = max(0, video_duration - watched_duration)

        if remaining <= 0:
            remaining = 60

        print(f"\n🎯 [{number}] 正在处理: {video_name}")
        print(f"   nodeId: {node_id} | 还需约 {video_duration}-{watched_duration}={remaining} 秒")

        sess = create_session(token)
        session_id = study_session_start(sess, user_id, node_id, course_id)
        if not session_id:
            print(f"⚠️ [{number}] 跳过该视频")
            return

        time.sleep(10 + random.uniform(0.5, 2))

        heartbeat_count = max(2, (remaining // 11) + 1)
        print(f"   💓 [{number}] 计划发送 {heartbeat_count} 次心跳")

        for i in range(heartbeat_count):
            success = study_session_heartbeat(sess, session_id)
            if not success:
                print(f"⚠️ [{number}] [{video_name}] 心跳失败（第 {i+1} 次）")
                break
            print(f"📊 [{number}] [{video_name}] 心跳进度: {i+1}/{heartbeat_count}")

            if i < heartbeat_count - 1:
                time.sleep(10 + random.uniform(0.5, 2))

        study_session_end(sess, session_id)
        print(f"✅ [{number}] 完成: {video_name}")

    except Exception as e:
        print(f"❌ [{number}] 处理异常: {e}")

# ================== 多线程入口 ==================
def process_course(token, number, user_id, course_id):
    print(f"\n{'='*50}")
    print(f"📚 [{number}] 开始处理课程: {course_id}")
    print(f"{'='*50}")
    incomplete_nodes = get_study_progress(token, user_id, course_id)

    if not incomplete_nodes:
        print(f"🎉 [{number}] 课程 {course_id} 所有视频已完成！")
        return

    actual_workers = min(MAX_WORKERS, len(incomplete_nodes))
    print(f"\n🚀 [{number}] 课程 {course_id}: 共 {len(incomplete_nodes)} 个视频待完成，使用 {actual_workers} 个线程并发刷课...\n")

    with ThreadPoolExecutor(max_workers=actual_workers) as executor:
        futures = [executor.submit(simulate_for_node, token, number, user_id, node, course_id) for node in incomplete_nodes]

        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"❌ [{number}] 线程异常: {e}")

    print(f"\n🎉 [{number}] 课程 {course_id} 所有未完成视频已处理完毕！")


# ================== 单账号处理 ==================
def process_account(account):
    number = account["number"]
    password = account["password"]
    course_ids = account.get("course_ids", [])

    print(f"\n{'='*60}")
    print(f"👤 开始处理账号: {number}")
    print(f"{'='*60}")

    try:
        token, user_id = login(number, password)
    except Exception as e:
        print(f"❌ 账号 {number} 登录失败: {e}")
        return

    print(f"✅ 账号 {number} 登录成功 | userId: {user_id}")

    if not course_ids:
        print(f"🔍 [{number}] 未配置课程ID，自动获取未过期课程...")
        course_ids = fetch_course_ids(token, user_id)
        if not course_ids:
            print(f"❌ [{number}] 没有找到未过期的课程")
            return
        print(f"📚 [{number}] 自动获取到 {len(course_ids)} 个未过期课程: {course_ids}")

    print(f"📚 [{number}] 共 {len(course_ids)} 个课程待处理: {course_ids}")

    for cid in course_ids:
        process_course(token, number, user_id, cid)

    print(f"\n🎉 账号 {number} 所有课程处理完毕！")


# ================== 启动 ==================
if __name__ == "__main__":
    if not ACCOUNTS:
        print("❌ 未配置任何账号，请在 config.py 中设置 ACCOUNTS")
    elif len(ACCOUNTS) == 1:
        process_account(ACCOUNTS[0])
    else:
        print(f"👥 共 {len(ACCOUNTS)} 个账号同时启动")
        with ThreadPoolExecutor(max_workers=len(ACCOUNTS)) as executor:
            futures = [executor.submit(process_account, acc) for acc in ACCOUNTS]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    print(f"❌ 账号处理异常: {e}")
        print("\n🎉 所有账号处理完毕！")
