---
name: wecom
description: 企业微信推送能力——给员工发消息、发任务卡片、群机器人通知、查询通讯录。当 AI 需要通知友联员工、推送数据预警、分派任务到企微、或者查询企微通讯录时使用本 skill。适用场景包括但不限于：库存预警推采购群、经营数据推管理群、任务分派给指定人、全员通知。即使用户没有明确说"发企微"，只要上下文涉及"通知某人"、"推送到群"、"告诉采购"等意图，都应使用。
---

# 企业微信推送 — wecom skill

## 核心能力

| 能力 | 说明 | 场景 |
|------|------|------|
| **应用消息** | 给指定人/部门/标签/全员发消息 | 任务分派、审批提醒、数据预警 |
| **应用群聊** | `appchat/create` 拉群 + `appchat/send` 群发 | 项目组讨论、临时拉人通知（**应用可见范围须为根部门**，见 union-agent `/api/wecom/appchat/*`） |
| **模板卡片** | 带按钮的交互卡片（跳转到 dashboard） | 任务确认、查看详情 |
| **群机器人** | 通过 Webhook 发消息到群 | 日报推送、告警通知 |
| **通讯录** | 查询部门和成员信息 | 找人、确认 userid |

## 配置

`shujian-brain/.env`：

```
WECOM_CORP_ID=ww...          # 企业ID
WECOM_CORP_SECRET=xxx...     # 应用Secret
WECOM_AGENT_ID=1000063       # 应用ID
WECOM_WEBHOOK_URL=           # 群机器人（可选）
```

**前置条件**：应用的「企业可信IP」需包含运行环境的公网 IP。

## 脚本路径

```
shujian-brain/.agents/skills/wecom/scripts/wecom.py
```

## 命令速查

### 发送消息

```bash
wecom.py send --to "userid1|userid2" --text "消息内容"
wecom.py send --to "@all" --text "全员通知"
wecom.py send --party "2" --text "发给部门"
wecom.py send --tag "1" --text "发给标签组"
wecom.py send --to "userid" --markdown "## 库存预警\n> 三诺易巧缺货率 **42%**"
```

### 任务卡片

```bash
wecom.py card --to "userid" --title "补货任务" --desc "三诺易巧缺货率42%" --url "https://dashboard/inventory"
wecom.py card --to "userid" --title "补货任务" --desc "请处理" \
  --buttons '[{"text":"查看详情","url":"https://a"},{"text":"我已处理","url":"https://b"}]'
```

### 群机器人

```bash
wecom.py webhook --text "消息"
wecom.py webhook --url "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx" --markdown "**内容**"
```

### 通讯录

```bash
wecom.py departments
wecom.py users --department 1
wecom.py user --id "userid"
```

### 应用群聊（服务端拉群）

```bash
wecom.py appchat create --users "userid1|userid2" --name "项目群"
wecom.py appchat send --chatid "<返回的chatid>" --text "大家好"
```

### 连通性测试

```bash
wecom.py test
```

## 与 union-dashboard 联动

友联员工表 `ads_employee` 已支持 `wecom_userid`。在 **union-agent** 配置 `WECOM_*` 后：

- 管理端 **员工管理** 页可点「同步企微通讯录」按手机号写入 `wecom_userid`
- API：`POST /api/wecom/send`（需登录），`employee_id` 指定接收人

本地脚本 `wecom.py` 仍可直接 `send --to <userid>`，userid 以库中 `wecom_userid` 为准最稳。

## 限制与注意

- **可信 IP**：自建应用需在管理后台配置企业可信 IP
- **频率**：应用消息约每分钟 200 条；群机器人约每分钟 20 条
- **零依赖**：纯 Python stdlib（urllib + json）
