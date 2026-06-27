import time
import re
import queue
import threading

import requests
from bs4 import BeautifulSoup
import streamlit as st

# ── 디시 로직 ─────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.dcinside.com/",
    "Origin": "https://www.dcinside.com",
}


def login(user_id, password):
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        session.get("https://www.dcinside.com/", timeout=10)
    except Exception as e:
        return None, f"사이트 접속 실패: {e}"
    try:
        session.post(
            "https://www.dcinside.com/member_login",
            data={"s_url": "https://www.dcinside.com/", "user_id": user_id, "pw": password},
            allow_redirects=True,
            timeout=10,
        )
    except Exception as e:
        return None, f"로그인 요청 실패: {e}"

    cookies = {c.name for c in session.cookies}
    if "user_id" in cookies or "dc_auth_verify" in cookies or "PHPSESSID" in cookies:
        return session, None
    try:
        r = session.get(f"https://gallog.dcinside.com/{user_id}/", timeout=10)
        if "로그아웃" in r.text or user_id in r.text:
            return session, None
    except Exception:
        pass
    return None, "아이디 또는 비밀번호를 확인하세요."


def collect_items(session, user_id, item_type, progress_callback=None):
    """
    item_type: 'posting' (글) | 'comment' (댓글)
    반환: [(gall_id, no), ...]
    """
    all_items = []
    page = 1
    while True:
        url = f"https://gallog.dcinside.com/{user_id}/{item_type}?p={page}"
        try:
            r = session.get(url, timeout=10)
        except Exception as e:
            if progress_callback:
                progress_callback(f"페이지 {page} 로드 실패: {e}")
            break

        soup = BeautifulSoup(r.text, "html.parser")
        items = []
        for a in soup.select("a[href*='board/view']"):
            href = a.get("href", "")
            gall_id = re.search(r"[?&]id=([^&]+)", href)
            no = re.search(r"[?&]no=(\d+)", href)
            if gall_id and no:
                items.append((gall_id.group(1), no.group(1)))

        if not items:
            break

        all_items.extend(items)
        if progress_callback:
            progress_callback(f"페이지 {page} 수집 완료 — 누적 {len(all_items)}개")

        page_nums = [
            int(a.text.strip())
            for a in soup.select(".pgn a")
            if a.text.strip().isdigit()
        ]
        if not page_nums or page >= max(page_nums):
            break
        page += 1

    return all_items


def delete_item(session, user_id, gall_id, no, log_type):
    """
    log_type: 'post' (글) | 'comment' (댓글)
    """
    url = f"https://gallog.dcinside.com/{user_id}/ajax/log_list_ajax/delete"
    try:
        r = session.post(
            url,
            data={"id": gall_id, "no": no, "log_type": log_type},
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"https://gallog.dcinside.com/{user_id}/posting",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
            timeout=10,
        )
        data = r.json()
        result = data.get("result", "")
        if "ok" in str(result) or result is True:
            return True, "삭제 완료"
        elif "captcha" in str(result):
            return False, "캡차 감지됨"
        else:
            return False, f"실패: {data.get('msg', result)}"
    except requests.exceptions.JSONDecodeError:
        return (True, "삭제 완료") if r.status_code == 200 else (False, f"HTTP {r.status_code}")
    except Exception as e:
        return False, str(e)


def run_deletion(user_id, password, delete_posts, delete_comments, progress_callback=None, delay=0.8):
    if progress_callback:
        progress_callback("로그인 중...")
    session, err = login(user_id, password)
    if err:
        return 0, 0, err

    if progress_callback:
        progress_callback("✓ 로그인 성공.")

    total_success, total_fail = 0, 0

    # 글 삭제
    if delete_posts:
        if progress_callback:
            progress_callback("글 목록 수집 중...")
        posts = collect_items(session, user_id, "posting", progress_callback)
        if posts:
            if progress_callback:
                progress_callback(f"글 {len(posts)}개 발견. 삭제 시작...")
            for i, (gall_id, no) in enumerate(posts, 1):
                ok, msg = delete_item(session, user_id, gall_id, no, "post")
                total_success += ok
                total_fail += not ok
                if progress_callback:
                    progress_callback(f"[글 {i}/{len(posts)}] {'✓' if ok else '✗'} {gall_id}/{no} — {msg}")
                time.sleep(delay)
        else:
            if progress_callback:
                progress_callback("삭제할 글 없음.")

    # 댓글 삭제
    if delete_comments:
        if progress_callback:
            progress_callback("댓글 목록 수집 중...")
        comments = collect_items(session, user_id, "comment", progress_callback)
        if comments:
            if progress_callback:
                progress_callback(f"댓글 {len(comments)}개 발견. 삭제 시작...")
            for i, (gall_id, no) in enumerate(comments, 1):
                ok, msg = delete_item(session, user_id, gall_id, no, "comment")
                total_success += ok
                total_fail += not ok
                if progress_callback:
                    progress_callback(f"[댓글 {i}/{len(comments)}] {'✓' if ok else '✗'} {gall_id}/{no} — {msg}")
                time.sleep(delay)
        else:
            if progress_callback:
                progress_callback("삭제할 댓글 없음.")

    return total_success, total_fail, None


# ── Streamlit UI ──────────────────────────────────────────────

st.set_page_config(page_title="디시 클리너", page_icon="🗑️", layout="centered")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=Noto+Sans+KR:wght@400;700&display=swap');
html, body, [class*="css"] { font-family: 'Noto Sans KR', sans-serif; }
.title-block { padding: 2rem 0 0.5rem 0; border-bottom: 3px solid #e63946; margin-bottom: 2rem; }
.title-block h1 { font-family: 'IBM Plex Mono', monospace; font-size: 2rem; font-weight: 600; color: #e63946; margin: 0; }
.title-block p { color: #555; font-size: 0.9rem; margin-top: 0.4rem; }
.log-box { background: #0d0d0d; color: #b5f2a0; font-family: 'IBM Plex Mono', monospace; font-size: 0.78rem; padding: 1rem 1.2rem; border-radius: 6px; max-height: 320px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; line-height: 1.6; }
.result-ok  { color: #2ecc71; font-weight: 700; font-size: 1.1rem; }
.result-err { color: #e63946; font-weight: 700; font-size: 1.1rem; }
.warn-box { background: #fff3cd; border-left: 4px solid #f0a500; padding: 0.8rem 1rem; border-radius: 4px; font-size: 0.85rem; color: #555; margin-bottom: 1.2rem; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="title-block">
  <h1>🗑️ dci-cleaner</h1>
  <p>디시인사이드 글 · 댓글 전체 삭제 — 일반 갤러리 · 마이너 갤러리 지원</p>
</div>
""", unsafe_allow_html=True)

st.markdown("""
<div class="warn-box">
  ⚠️ <b>삭제된 글·댓글은 복구되지 않습니다.</b><br>
  비밀번호는 디시 서버로만 전달되며 저장되지 않습니다.
</div>
""", unsafe_allow_html=True)

for k, v in [("running", False), ("logs", []), ("result", None)]:
    if k not in st.session_state:
        st.session_state[k] = v

col1, col2 = st.columns(2)
with col1:
    user_id = st.text_input("디시 아이디", placeholder="your_id", disabled=st.session_state.running)
with col2:
    password = st.text_input("비밀번호", type="password", placeholder="••••••••", disabled=st.session_state.running)

col3, col4 = st.columns(2)
with col3:
    delete_posts = st.checkbox("글 삭제", value=True, disabled=st.session_state.running)
with col4:
    delete_comments = st.checkbox("댓글 삭제", value=True, disabled=st.session_state.running)

delay = st.slider("요청 딜레이 (초)", 0.5, 3.0, 0.8, 0.1, disabled=st.session_state.running)

start_btn = st.button(
    "🗑️ 삭제 시작",
    disabled=st.session_state.running or not user_id or not password or (not delete_posts and not delete_comments),
    use_container_width=True,
    type="primary",
)

log_placeholder = st.empty()
result_placeholder = st.empty()


def render_logs():
    log_text = "\n".join(st.session_state.logs[-300:])
    log_placeholder.markdown(f'<div class="log-box">{log_text}</div>', unsafe_allow_html=True)


def run_in_thread(uid, pw, dp, dc, q, d):
    def cb(msg):
        q.put(("log", msg))
    try:
        result = run_deletion(uid, pw, dp, dc, progress_callback=cb, delay=d)
        q.put(("done", result))
    except Exception as e:
        q.put(("done", (0, 0, str(e))))


if start_btn and not st.session_state.running:
    st.session_state.running = True
    st.session_state.logs = []
    st.session_state.result = None

    q = queue.Queue()
    threading.Thread(
        target=run_in_thread,
        args=(user_id, password, delete_posts, delete_comments, q, delay),
        daemon=True,
    ).start()

    while True:
        try:
            msg_type, payload = q.get(timeout=0.3)
        except queue.Empty:
            render_logs()
            continue
        if msg_type == "log":
            st.session_state.logs.append(payload)
            render_logs()
        elif msg_type == "done":
            st.session_state.result = payload
            st.session_state.running = False
            break

    render_logs()
    success, fail, err = st.session_state.result
    if err and success == 0:
        result_placeholder.markdown(f'<p class="result-err">❌ 오류: {err}</p>', unsafe_allow_html=True)
    else:
        result_placeholder.markdown(
            f'<p class="result-ok">✅ 완료 — 성공 {success}개 / 실패 {fail}개'
            + (f" | {err}" if err else "") + "</p>",
            unsafe_allow_html=True,
        )

elif st.session_state.logs:
    render_logs()
    if st.session_state.result:
        success, fail, err = st.session_state.result
        if err and success == 0:
            result_placeholder.markdown(f'<p class="result-err">❌ 오류: {err}</p>', unsafe_allow_html=True)
        else:
            result_placeholder.markdown(
                f'<p class="result-ok">✅ 완료 — 성공 {success}개 / 실패 {fail}개'
                + (f" | {err}" if err else "") + "</p>",
                unsafe_allow_html=True,
            )
