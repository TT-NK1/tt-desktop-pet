"""Safe summaries of Responses SSE failures; never log a request payload."""
import re


def response_error(event):
    response = event.get("response") or {}
    detail = response.get("error") or event.get("error") or event
    if not isinstance(detail, dict):
        detail = {}
    code = detail.get("code") or detail.get("type") or event.get("code") or event.get("type", "response_error")
    code = str(code)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", code):
        code = "response_error"
    message = str(detail.get("message", ""))[:1000]
    param = str(detail.get("param") or "")
    combined = (code + " " + message + " " + param).lower()
    if "subscription" in combined or "quota" in combined or "rate_limit" in combined:
        summary = "套餐额度或调用频率受限，请检查 ChatGPT 账户设置。"
    elif "web_search" in combined or "tool" in param.lower() or "unsupported_tool" in combined:
        summary = "当前模型或账户的联网工具请求未被接受。先关闭两个联网搜索开关，再发一句普通消息验证。"
    elif "token" in combined or "authentication" in combined or "unauthorized" in combined:
        summary = "登录凭证未被接受，请重新读取模型，必要时重新授权。"
    elif "server" in combined or "internal" in combined:
        summary = "服务端未完成请求，请稍后手动重试。"
    elif "context" in combined or "too_long" in combined:
        summary = "本次上下文超过模型限制，请开启新话题后重试。"
    else:
        summary = "接口拒绝或未完成本次请求，请保留下面的错误代码以便定位。"
    # Show identifiers only, not arbitrary error messages that might echo inputs.
    fields = [code]
    if param and re.fullmatch(r"[A-Za-z0-9_.\[\]-]{1,100}", param):
        fields.append("param=" + param)
    return summary + "（" + "; ".join(fields) + "）"
