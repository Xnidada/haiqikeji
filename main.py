# ============================================================
# Author：X_ni_dada
# GitHub：https://github.com/Xnidada/haiqikeji
# 注意事项：本脚本仅供学习和技术研究使用，请遵守相关平台的使用规定。
# ============================================================
import requests
import time
import re

# ================== 配置 ===================
API_BASE = "https://swxy.haiqikeji.com/api/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36 Edg/146.0.0.0",
    "Content-Type": "application/json",
    "Authorization": "",
    "Referer": "https://swxy.haiqikeji.com/student/course-study-record?id=1012270",
    "Origin": "https://swxy.haiqikeji.com",
    "Cookie": "__root_domain_v=.haiqikeji.com; _qddaz=QD.797773064926835"
}

SCHOOL_ID = 15
USER_ID = 1283729
COURSE_ID = 1012270


# ================== 工具函数：解析时间字符串 ==================
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


# ================== 获取学习进度 ==================
def get_study_progress():
    url = f"{API_BASE}/user/get_study_progress"
    params = {
        "schoolId": SCHOOL_ID,
        "userId": USER_ID,
        "courseId": COURSE_ID
    }
    resp = requests.get(url, headers=HEADERS, params=params)
    print(f"[DEBUG] Get Progress Response: {resp.status_code}")

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


# ================== API 函数 ==================
def study_session_start(node_id: int):
    url = f"{API_BASE}/user/study_session_start"
    payload = {
        "schoolId": SCHOOL_ID,
        "userId": USER_ID,
        "courseId": COURSE_ID,
        "nodeId": node_id,
        "terminal": "web"
    }
    resp = requests.post(url, headers=HEADERS, json=payload)
    if resp.status_code == 200 and resp.json().get("code") == 200:
        return resp.json().get("data")
    print("❌ 会话启动失败")
    return None


def study_session_heartbeat(session_id):
    url = f"{API_BASE}/user/study_session_heartbeat"
    payload = {"sessionId": session_id}
    resp = requests.post(url, headers=HEADERS, json=payload)
    if resp.status_code == 200:
        try:
            data = resp.json()
            if data.get("code") == 200:
                return True
        except:
            pass
    return False


def study_session_end(session_id):
    url = f"{API_BASE}/user/study_session_end"
    payload = {"sessionId": session_id}
    requests.post(url, headers=HEADERS, json=payload)


def simulate_for_node(node):
    node_id = node["nodeId"]
    video_name = node["nodeName"]
    video_duration = parse_duration(node["videoDuration"])
    watched_duration = parse_duration(node["watchDuration"])
    remaining = max(0, video_duration - watched_duration)

    if remaining <= 0:
        remaining = 60

    print(f"\n🎯 正在处理: {video_name}")
    print(f"   nodeId: {node_id} | 还需约 {remaining} 秒")

    session_id = study_session_start(node_id)
    if not session_id:
        print("⚠️ 跳过该视频")
        return

    # 第一次心跳前等 11 秒
    time.sleep(11)

    # 计算心跳次数：每 11 秒一次
    heartbeat_count = max(2, (remaining // 11) + 1)
    print(f"   💓 计划发送 {heartbeat_count} 次心跳（间隔11秒）")

    for i in range(heartbeat_count):
        success = study_session_heartbeat(session_id)
        if not success:
            print("⚠️ 心跳失败，跳过")
            break
        if i < heartbeat_count - 1:
            time.sleep(11)

    study_session_end(session_id)
    print(f"✅ 完成: {video_name}")

# ================== 主流程：遍历所有未完成视频 ==================
def simulate_all_incomplete():
    print("🔍 正在获取课程学习进度...")
    incomplete_nodes = get_study_progress()

    if not incomplete_nodes:
        print("🎉 所有视频已完成！")
        return

    print(f"\n🚀 总共 {len(incomplete_nodes)} 个视频待完成，开始自动刷课...\n")

    for idx, node in enumerate(incomplete_nodes, 1):
        print(f"\n[{idx}/{len(incomplete_nodes)}]")
        simulate_for_node(node)

    print("\n🎉 所有未完成视频已处理完毕！")


if __name__ == "__main__":
    simulate_all_incomplete()