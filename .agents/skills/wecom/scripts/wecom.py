#!/usr/bin/env python3
"""
企业微信客户端 — shujian-brain 的企微推送能力
零外部依赖，纯 stdlib (urllib + json)。

用法：
    python3 wecom.py send --to "userid1|userid2" --text "消息内容"
    python3 wecom.py send --to "@all" --markdown "## 标题\n内容"
    python3 wecom.py send --party "2" --text "部门通知"
    python3 wecom.py send --tag "1" --text "标签组通知"
    python3 wecom.py card --to "userid" --title "任务标题" --desc "描述" --url "https://xxx" --btn "查看详情"
    python3 wecom.py webhook --url "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx" --markdown "内容"
    python3 wecom.py users --department 1
    python3 wecom.py departments
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

BASE_URL = "https://qyapi.weixin.qq.com/cgi-bin"


def load_env():
    """从 shujian-brain/.env 加载配置"""
    env_path = Path(__file__).resolve().parents[4] / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env()


def get_config():
    corp_id = os.environ.get("WECOM_CORP_ID", "")
    corp_secret = os.environ.get("WECOM_CORP_SECRET", "")
    agent_id = os.environ.get("WECOM_AGENT_ID", "")
    if not corp_id or not corp_secret:
        print("错误: 缺少 WECOM_CORP_ID 或 WECOM_CORP_SECRET，请配置 shujian-brain/.env", file=sys.stderr)
        sys.exit(1)
    return corp_id, corp_secret, agent_id


def http_get(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def http_post(url, data):
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


_token_cache_path = Path(__file__).parent / ".wecom_token_cache.json"


def _read_cache():
    if _token_cache_path.exists():
        try:
            data = json.loads(_token_cache_path.read_text())
            if data.get("expires_at", 0) > time.time() + 60:
                return data["access_token"]
        except (json.JSONDecodeError, KeyError):
            pass
    return None


def _write_cache(token, expires_in):
    _token_cache_path.write_text(json.dumps({
        "access_token": token,
        "expires_at": time.time() + expires_in - 120
    }))


def get_access_token():
    cached = _read_cache()
    if cached:
        return cached
    corp_id, corp_secret, _ = get_config()
    url = f"{BASE_URL}/gettoken?corpid={corp_id}&corpsecret={urllib.parse.quote(corp_secret)}"
    resp = http_get(url)
    if resp.get("errcode", 0) != 0:
        print(f"获取 access_token 失败: {resp}", file=sys.stderr)
        sys.exit(1)
    _write_cache(resp["access_token"], resp["expires_in"])
    return resp["access_token"]


def send_message(msgtype, content, to_user=None, to_party=None, to_tag=None):
    _, _, agent_id = get_config()
    if not agent_id:
        print("错误: 缺少 WECOM_AGENT_ID", file=sys.stderr)
        sys.exit(1)

    payload = {
        "agentid": int(agent_id),
        "msgtype": msgtype,
        **content,
    }
    if to_user:
        payload["touser"] = to_user
    if to_party:
        payload["toparty"] = to_party
    if to_tag:
        payload["totag"] = to_tag
    if not to_user and not to_party and not to_tag:
        payload["touser"] = "@all"

    token = get_access_token()
    url = f"{BASE_URL}/message/send?access_token={token}"
    resp = http_post(url, payload)

    if resp.get("errcode", 0) != 0:
        print(f"发送失败: {resp}", file=sys.stderr)
        return False
    print(f"✅ 发送成功 (msgtype={msgtype}, to={to_user or to_party or to_tag or '@all'})")
    return True


def send_text(text, **kwargs):
    return send_message("text", {"text": {"content": text}}, **kwargs)


def send_markdown(md, **kwargs):
    return send_message("markdown", {"markdown": {"content": md}}, **kwargs)


def send_textcard(title, description, url, btntxt="详情", **kwargs):
    return send_message("textcard", {"textcard": {
        "title": title,
        "description": description,
        "url": url,
        "btntxt": btntxt,
    }}, **kwargs)


def send_template_card(card_data, **kwargs):
    return send_message("template_card", {"template_card": card_data}, **kwargs)


def send_task_card(title, desc, url, btn_text="查看详情", **kwargs):
    card = {
        "card_type": "text_notice",
        "main_title": {"title": title},
        "sub_title_text": desc,
        "card_action": {"type": 1, "url": url},
        "horizontal_content_list": [],
    }
    return send_template_card(card, **kwargs)


def send_button_card(title, desc, buttons, **kwargs):
    card = {
        "card_type": "button_interaction",
        "main_title": {"title": title},
        "sub_title_text": desc,
        "button_list": [
            {"text": b["text"], "type": 1, "url": b["url"]}
            for b in buttons
        ],
    }
    return send_template_card(card, **kwargs)


def webhook_send(webhook_url, msgtype, content):
    payload = {"msgtype": msgtype, **content}
    resp = http_post(webhook_url, payload)
    if resp.get("errcode", 0) != 0:
        print(f"Webhook 发送失败: {resp}", file=sys.stderr)
        return False
    print(f"✅ Webhook 发送成功 (msgtype={msgtype})")
    return True


def webhook_text(webhook_url, text, mentioned_list=None, mentioned_mobile_list=None):
    content = {"text": {"content": text}}
    if mentioned_list:
        content["text"]["mentioned_list"] = mentioned_list
    if mentioned_mobile_list:
        content["text"]["mentioned_mobile_list"] = mentioned_mobile_list
    return webhook_send(webhook_url, "text", content)


def webhook_markdown(webhook_url, md):
    return webhook_send(webhook_url, "markdown", {"markdown": {"content": md}})


def get_department_list(dept_id=None):
    token = get_access_token()
    url = f"{BASE_URL}/department/list?access_token={token}"
    if dept_id is not None:
        url += f"&id={dept_id}"
    return http_get(url)


def get_department_users(dept_id):
    token = get_access_token()
    url = f"{BASE_URL}/user/simplelist?access_token={token}&department_id={dept_id}"
    return http_get(url)


def get_department_users_detail(dept_id):
    token = get_access_token()
    url = f"{BASE_URL}/user/list?access_token={token}&department_id={dept_id}"
    return http_get(url)


def get_user(userid):
    token = get_access_token()
    url = f"{BASE_URL}/user/get?access_token={token}&userid={userid}"
    return http_get(url)


def print_help():
    print("""
企业微信客户端 — wecom.py

发送消息:
  wecom.py send --to "userid" --text "消息"
  wecom.py send --to "userid" --markdown "**加粗**内容"
  wecom.py send --to "@all" --text "全员通知"
  wecom.py send --party "2" --text "部门通知"
  wecom.py send --tag "1" --text "标签通知"

任务卡片:
  wecom.py card --to "userid" --title "标题" --desc "描述" --url "https://xxx"
  wecom.py card --to "userid" --title "标题" --desc "描述" --buttons '[{"text":"确认","url":"https://a"},{"text":"拒绝","url":"https://b"}]'

群机器人:
  wecom.py webhook --url "webhook_url" --text "消息"
  wecom.py webhook --url "webhook_url" --markdown "**内容**"
  wecom.py webhook --url "webhook_url" --text "消息" --mention "@all"

通讯录:
  wecom.py departments
  wecom.py users --department 1
  wecom.py user --id "userid"

应用群聊（appchat/create、appchat/send，应用可见范围须为根部门）:
  wecom.py appchat create --users "userid1|userid2" [--name "群名"] [--owner userid] [--chatid 自定义ID]
  wecom.py appchat send --chatid CHATID --text "消息"
  wecom.py appchat send --chatid CHATID --markdown "**标题**"

测试:
  wecom.py test
""")


def cmd_send(args):
    to_user = to_party = to_tag = None
    text = markdown = None
    i = 0
    while i < len(args):
        if args[i] == "--to" and i + 1 < len(args):
            to_user = args[i + 1]; i += 2
        elif args[i] == "--party" and i + 1 < len(args):
            to_party = args[i + 1]; i += 2
        elif args[i] == "--tag" and i + 1 < len(args):
            to_tag = args[i + 1]; i += 2
        elif args[i] == "--text" and i + 1 < len(args):
            text = args[i + 1]; i += 2
        elif args[i] == "--markdown" and i + 1 < len(args):
            markdown = args[i + 1]; i += 2
        else:
            i += 1

    kwargs = {}
    if to_user:
        kwargs["to_user"] = to_user
    if to_party:
        kwargs["to_party"] = to_party
    if to_tag:
        kwargs["to_tag"] = to_tag

    if markdown:
        send_markdown(markdown, **kwargs)
    elif text:
        send_text(text, **kwargs)
    else:
        print("错误: 需要 --text 或 --markdown", file=sys.stderr)


def cmd_card(args):
    to_user = to_party = None
    title = desc = url = ""
    btn_text = "查看详情"
    buttons_json = None
    i = 0
    while i < len(args):
        if args[i] == "--to" and i + 1 < len(args):
            to_user = args[i + 1]; i += 2
        elif args[i] == "--party" and i + 1 < len(args):
            to_party = args[i + 1]; i += 2
        elif args[i] == "--title" and i + 1 < len(args):
            title = args[i + 1]; i += 2
        elif args[i] == "--desc" and i + 1 < len(args):
            desc = args[i + 1]; i += 2
        elif args[i] == "--url" and i + 1 < len(args):
            url = args[i + 1]; i += 2
        elif args[i] == "--btn" and i + 1 < len(args):
            btn_text = args[i + 1]; i += 2
        elif args[i] == "--buttons" and i + 1 < len(args):
            buttons_json = args[i + 1]; i += 2
        else:
            i += 1

    kwargs = {}
    if to_user:
        kwargs["to_user"] = to_user
    if to_party:
        kwargs["to_party"] = to_party

    if buttons_json:
        buttons = json.loads(buttons_json)
        send_button_card(title, desc, buttons, **kwargs)
    elif url:
        send_task_card(title, desc, url, btn_text, **kwargs)
    else:
        print("错误: 需要 --url 或 --buttons", file=sys.stderr)


def cmd_webhook(args):
    webhook_url = text = markdown = mention = None
    i = 0
    while i < len(args):
        if args[i] == "--url" and i + 1 < len(args):
            webhook_url = args[i + 1]; i += 2
        elif args[i] == "--text" and i + 1 < len(args):
            text = args[i + 1]; i += 2
        elif args[i] == "--markdown" and i + 1 < len(args):
            markdown = args[i + 1]; i += 2
        elif args[i] == "--mention" and i + 1 < len(args):
            mention = args[i + 1]; i += 2
        else:
            i += 1

    if not webhook_url:
        webhook_url = os.environ.get("WECOM_WEBHOOK_URL", "")
    if not webhook_url:
        print("错误: 需要 --url 或配置 WECOM_WEBHOOK_URL", file=sys.stderr)
        return

    if markdown:
        webhook_markdown(webhook_url, markdown)
    elif text:
        mentioned = mention.split(",") if mention else None
        webhook_text(webhook_url, text, mentioned_list=mentioned)
    else:
        print("错误: 需要 --text 或 --markdown", file=sys.stderr)


def cmd_departments(args):
    resp = get_department_list()
    if resp.get("errcode", 0) != 0:
        print(f"查询失败: {resp}", file=sys.stderr)
        return
    depts = resp.get("department", [])
    print(f"共 {len(depts)} 个部门:\n")
    for d in sorted(depts, key=lambda x: x.get("id", 0)):
        parent = d.get("parentid", "")
        print(f"  [{d['id']}] {d['name']}  (上级: {parent})")


def cmd_users(args):
    dept_id = 1
    detail = False
    i = 0
    while i < len(args):
        if args[i] == "--department" and i + 1 < len(args):
            dept_id = int(args[i + 1]); i += 2
        elif args[i] == "--detail":
            detail = True; i += 1
        else:
            i += 1

    if detail:
        resp = get_department_users_detail(dept_id)
        users = resp.get("userlist", [])
        print(f"部门 {dept_id} 共 {len(users)} 人:\n")
        for u in users:
            status = "✅" if u.get("status", 0) == 1 else "❌"
            dept_ids = u.get("department", [])
            print(f"  {status} {u.get('userid', '?')} | {u.get('name', '?')} | {u.get('position', '-')} | 部门{dept_ids}")
    else:
        resp = get_department_users(dept_id)
        users = resp.get("userlist", [])
        print(f"部门 {dept_id} 共 {len(users)} 人:\n")
        for u in users:
            print(f"  {u.get('userid', '?')} | {u.get('name', '?')}")


def cmd_user(args):
    userid = None
    i = 0
    while i < len(args):
        if args[i] == "--id" and i + 1 < len(args):
            userid = args[i + 1]; i += 2
        else:
            i += 1
    if not userid:
        print("错误: 需要 --id", file=sys.stderr)
        return
    resp = get_user(userid)
    if resp.get("errcode", 0) != 0:
        print(f"查询失败: {resp}", file=sys.stderr)
        return
    print(json.dumps(resp, ensure_ascii=False, indent=2))


def cmd_test(args):
    print("1. 获取 access_token...")
    token = get_access_token()
    print(f"   ✅ token: {token[:20]}...{token[-10:]}")

    print("\n2. 查询部门列表...")
    resp = get_department_list()
    if resp.get("errcode", 0) == 0:
        depts = resp.get("department", [])
        print(f"   ✅ 共 {len(depts)} 个部门")
        for d in sorted(depts, key=lambda x: x.get("id", 0))[:5]:
            print(f"      [{d['id']}] {d['name']}")
        if len(depts) > 5:
            print(f"      ... 还有 {len(depts) - 5} 个")
    else:
        print(f"   ❌ {resp}")

    print("\n3. 查询根部门成员...")
    resp = get_department_users(1)
    if resp.get("errcode", 0) == 0:
        users = resp.get("userlist", [])
        print(f"   ✅ 根部门 {len(users)} 人")
        for u in users[:5]:
            print(f"      {u.get('userid')} | {u.get('name')}")
        if len(users) > 5:
            print(f"      ... 还有 {len(users) - 5} 人")
    else:
        print(f"   ❌ {resp}")

    print("\n连通性测试完成。")
    print("发送测试消息: wecom.py send --to \"你的userid\" --text \"Hello from AI\"")


def cmd_appchat(args):
    if not args:
        print("用法: wecom.py appchat create ... | appchat send ...", file=sys.stderr)
        return
    sub = args[0]
    rest = args[1:]
    if sub == "create":
        name = owner = chatid = None
        users_raw = None
        i = 0
        while i < len(rest):
            if rest[i] == "--name" and i + 1 < len(rest):
                name = rest[i + 1]
                i += 2
            elif rest[i] == "--users" and i + 1 < len(rest):
                users_raw = rest[i + 1]
                i += 2
            elif rest[i] == "--owner" and i + 1 < len(rest):
                owner = rest[i + 1]
                i += 2
            elif rest[i] == "--chatid" and i + 1 < len(rest):
                chatid = rest[i + 1]
                i += 2
            else:
                i += 1
        if not users_raw:
            print("错误: 需要 --users \"userid1|userid2|...\"", file=sys.stderr)
            return
        userlist = [u.strip() for u in users_raw.split("|") if u.strip()]
        if len(userlist) < 2:
            print("错误: 至少需要 2 个 userid", file=sys.stderr)
            return
        token = get_access_token()
        url = f"{BASE_URL}/appchat/create?access_token={token}"
        body: dict = {"userlist": userlist}
        if name:
            body["name"] = name
        if owner:
            body["owner"] = owner
        if chatid:
            body["chatid"] = chatid
        resp = http_post(url, body)
        if resp.get("errcode", 0) != 0:
            print(f"创建失败: {resp}", file=sys.stderr)
            return
        print(f"✅ 群已创建 chatid={resp.get('chatid')}")
    elif sub == "send":
        chatid = text = markdown = None
        i = 0
        while i < len(rest):
            if rest[i] == "--chatid" and i + 1 < len(rest):
                chatid = rest[i + 1]
                i += 2
            elif rest[i] == "--text" and i + 1 < len(rest):
                text = rest[i + 1]
                i += 2
            elif rest[i] == "--markdown" and i + 1 < len(rest):
                markdown = rest[i + 1]
                i += 2
            else:
                i += 1
        if not chatid:
            print("错误: 需要 --chatid", file=sys.stderr)
            return
        if bool(text) == bool(markdown):
            print("错误: 需要 --text 或 --markdown 之一", file=sys.stderr)
            return
        token = get_access_token()
        url = f"{BASE_URL}/appchat/send?access_token={token}"
        if text:
            body = {"chatid": chatid, "msgtype": "text", "text": {"content": text},
                    "safe": 0}
        else:
            body = {"chatid": chatid, "msgtype": "markdown",
                    "markdown": {"content": markdown}, "safe": 0}
        resp = http_post(url, body)
        if resp.get("errcode", 0) != 0:
            print(f"发送失败: {resp}", file=sys.stderr)
            return
        print("✅ 群消息已发送")
    else:
        print(f"未知子命令: {sub}（支持 create / send）", file=sys.stderr)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print_help()
    elif args[0] == "send":
        cmd_send(args[1:])
    elif args[0] == "card":
        cmd_card(args[1:])
    elif args[0] == "webhook":
        cmd_webhook(args[1:])
    elif args[0] == "departments":
        cmd_departments(args[1:])
    elif args[0] == "users":
        cmd_users(args[1:])
    elif args[0] == "user":
        cmd_user(args[1:])
    elif args[0] == "test":
        cmd_test(args[1:])
    elif args[0] == "appchat":
        cmd_appchat(args[1:])
    else:
        print(f"未知命令: {args[0]}", file=sys.stderr)
        print_help()
