#!/usr/bin/env python3
import argparse, json, os, random, re, time
from urllib.parse import unquote
from datetime import datetime
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BASE_DIR, "mobile_token.json")

CLIENT_ID = "0000000040170455"
AUTH_URL = "https://login.live.com/oauth20_authorize.srf"
TOKEN_URL = "https://login.live.com/oauth20_token.srf"
REDIRECT_URI = "https://login.live.com/oauth20_desktop.srf"
SCOPE = "service::prod.rewardsplatform.microsoft.com::MBI_SSL"

DASHBOARD_URL = "https://prod.rewardsplatform.microsoft.com/dapi/me"
ACTIVITIES_URL = "https://prod.rewardsplatform.microsoft.com/dapi/me/activities"
SEARCH_URL = "https://www.bing.com/search"
MOBILE_UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36 Edge/120.0.0.0")
SEARCH_KEYWORDS = ["today news","weather forecast","python tutorial","space exploration",
    "healthy recipes","latest technology","history facts","music charts","car reviews",
    "home workout","photography tips","cpu benchmark","gpu comparison","book recommendations"]
EXTRA_OFFERS = ["Gamification_Sapphire_DailyCheckIn"]

def log(msg, level="INFO"):
    ts = datetime.now().strftime("%H:%M:%S")
    mark = {"INFO":"i","OK":"OK","WARN":"!","ERR":"X","STEP":">"}[level]
    print(f"[{ts}] {mark} {msg}", flush=True)

def _new_session():
    s = requests.Session()
    s.headers.update({"User-Agent": MOBILE_UA, "Accept-Language":"zh-CN,zh;q=0.9,en;q=0.8"})
    return s

def _save_token(data):
    data["_saved_at"] = int(time.time())
    with open(TOKEN_PATH,"w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2)
    log(f"token 已缓存到 {TOKEN_PATH}","OK")

def _load_token():
    if not os.path.exists(TOKEN_PATH): return None
    try:
        with open(TOKEN_PATH,encoding="utf-8") as f: return json.load(f)
    except Exception: return None

def _exchange_code(session, code):
    resp = session.post(TOKEN_URL, data={
        "grant_type":"authorization_code","client_id":CLIENT_ID,"code":code,
        "redirect_uri":REDIRECT_URI,"scope":SCOPE},
        headers={"Content-Type":"application/x-www-form-urlencoded"}, timeout=30)
    if resp.status_code != 200:
        log(f"换 token 返回 {resp.status_code}: {resp.text[:300]}","ERR")
    resp.raise_for_status()
    return resp.json()

def _refresh_token(session, rt):
    resp = session.post(TOKEN_URL, data={
        "grant_type":"refresh_token","client_id":CLIENT_ID,"refresh_token":rt,"scope":SCOPE},
        headers={"Content-Type":"application/x-www-form-urlencoded"}, timeout=30)
    if resp.status_code != 200:
        log(f"续期返回 {resp.status_code}: {resp.text[:300]}","ERR")
    resp.raise_for_status()
    return resp.json()

def get_access_token(session, force_login=False, code_file=None):
    cached = None if force_login else _load_token()
    if cached and cached.get("refresh_token"):
        try:
            log("尝试用 refresh_token 续期...","STEP")
            data = _refresh_token(session, cached["refresh_token"])
            data["refresh_token"] = data.get("refresh_token") or cached["refresh_token"]
            _save_token(data)
            return data["access_token"]
        except Exception as e:
            log(f"续期失败：{e}，需要重新登录","WARN")

    state = "".join(random.choice("0123456789abcdef") for _ in range(16))
    auth_link = (f"{AUTH_URL}?client_id={CLIENT_ID}&scope={SCOPE}"
                 f"&response_type=code&redirect_uri={REDIRECT_URI}"
                 f"&access_type=offline_access&state={state}")
    print("\n"+"="*60)
    print("需要登录。用电脑浏览器打开下面链接登录并点接受：")
    print(f"\n{auth_link}\n")
    print("登录后 F12->Network->Preserve log->刷新，复制 oauth20_desktop.srf 请求的完整 Request URL")
    print("把该 URL 存进 code.txt，再跑： python3 mr.py --login --code-file code.txt")
    print("="*60+"\n")

    if code_file:
        with open(code_file,encoding="utf-8") as f:
            content = f.read()
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        redirect = next((ln for ln in lines if "code=" in ln), (lines[0] if lines else ""))
        if not redirect:
            raise SystemExit(f"{code_file} 是空的！请把含 code= 的 URL 写进去再跑。")
        log(f"已从文件读取重定向 URL: {code_file}","OK")
    else:
        redirect = input("粘贴重定向 URL: ").strip().strip("'\"")

    m = re.search(r"[?&]code=([^&]+)", redirect)
    if not m:
        raise SystemExit("未找到 code。请用电脑 F12 网络面板拿含 code= 的 URL 再试。")
    code = unquote(m.group(1))
    log(f"提取到 code（长度 {len(code)}，尾部 ...{code[-6:]}）","INFO")
    data = _exchange_code(session, code)
    _save_token(data)
    return data["access_token"]

def _auth(token): return {"Authorization": f"Bearer {token}"}

def get_dashboard(session, token):
    try:
        r = session.get(DASHBOARD_URL, headers=_auth(token), timeout=30)
        r.raise_for_status(); return r.json()
    except Exception as e:
        log(f"查询 dashboard 失败：{e}","WARN"); return None

def do_checkin(session, token):
    log("执行移动签到...","STEP"); ok=False
    for offer in EXTRA_OFFERS:
        try:
            r = session.post(ACTIVITIES_URL, headers={**_auth(token),"Content-Type":"application/json"},
                             json={"offerId":offer}, timeout=30)
            if r.status_code in (200,201):
                log(f"  签到成功 (offer={offer})","OK"); ok=True
            else:
                log(f"  offer={offer} 返回 {r.status_code}: {r.text[:120]}","WARN")
        except Exception as e:
            log(f"  签到异常：{e}","WARN")
    return ok

def do_search(session, token, count=15):
    log(f"执行移动搜索 x{count}...","STEP"); done=0
    for i in range(count):
        kw = random.choice(SEARCH_KEYWORDS)
        try:
            r = session.get(SEARCH_URL, params={"q":kw,"form":"QBLH"},
                            headers={**_auth(token),"Referer":"https://www.bing.com/"}, timeout=20)
            if r.status_code==200:
                done+=1; log(f"  [{done}/{count}] 搜索：{kw}","OK")
            else:
                log(f"  搜索返回 {r.status_code}","WARN")
        except Exception as e:
            log(f"  搜索异常：{e}","WARN")
        time.sleep(random.uniform(1.5,4.0))
    return done

def report(dash):
    if not dash: return
    try:
        user = dash.get("userInfo") or dash.get("user") or {}
        pts = user.get("availablePoints") or user.get("points")
        if pts is not None: log(f"当前可用积分：{pts}","INFO")
    except Exception: pass

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-search", action="store_true")
    ap.add_argument("--login", action="store_true")
    ap.add_argument("--count", type=int, default=15)
    ap.add_argument("--code-file", dest="code_file", default=None)
    args = ap.parse_args()

    log("=== 微软 Rewards 移动端任务开始 ===","STEP")
    session = _new_session()
    try:
        token = get_access_token(session, force_login=args.login, code_file=args.code_file)
        log("access_token 获取成功","OK")
    except SystemExit: raise
    except Exception as e:
        log(f"获取 token 失败：{e}","ERR"); return

    report(get_dashboard(session, token))
    do_checkin(session, token)
    if not args.no_search:
        do_search(session, token, args.count)
    log("--- 执行后状态 ---","STEP")
    report(get_dashboard(session, token))
    log("=== 移动端任务结束 ===","OK")

if __name__ == "__main__":
    main()
