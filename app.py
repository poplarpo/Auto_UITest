import sys
import asyncio
import streamlit as st
import os
import re
import subprocess
from datetime import datetime
from openai import OpenAI

# 解决 Windows 异步策略问题
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

# ==========================================
# 🔑 全局安全配置：在此设置你的平台密钥
# ==========================================
PLATFORM_ACCESS_KEY = "666888"  # 👈 你可以修改为任何你想要的密钥

# ==========================================
# 🌟 1. 页面全局配置与全局 CSS 美化
# ==========================================
st.set_page_config(page_title="Auto-Agent 自动化测试", page_icon="🤖", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
/* 魔法缩放：直接在代码层面实现全局 90% 视觉比例 */
html { zoom: 0.9; }
.block-container { padding-top: 2rem !important; padding-bottom: 1rem !important; max-width: 98% !important; }
.stDeployButton {display:none;}
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
div[data-testid="stChatInput"] { position: static !important; margin-top: 15px !important; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# 🔐 身份验证逻辑：必须在所有业务代码之前执行
# ==========================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    # 显示登录界面
    st.markdown("<h2 style='text-align: center;'>🔐 欢迎使用 Auto-Agent 平台</h2>", unsafe_allow_html=True)
    st.write("")

    # 居中显示输入框
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        input_key = st.text_input("请输入平台访问密钥以开启功能：", type="password")
        login_btn = st.button("立即进入平台", use_container_width=True, type="primary")

        if login_btn:
            if input_key == PLATFORM_ACCESS_KEY:
                st.session_state.authenticated = True
                st.success("验证成功！正在进入平台...")
                st.rerun()  # 重新运行以展示主界面
            else:
                st.error("密钥错误，请联系管理员获取正确密钥。")

    # 阻断后续代码执行
    st.stop()

# ==========================================
# 2. 平台主界面 (只有验证通过才会运行到这里)
# ==========================================
st.markdown(
    "<h3 style='margin-bottom: 5px; margin-top: 0px; font-weight: 600;'>🤖 Auto-Agent: 对话式 UI 自动化测试</h3>",
    unsafe_allow_html=True)
st.markdown(
    "<p style='color: #666; font-size: 14px; margin-top: 0px; margin-bottom: 15px;'>认证状态：已授权 | 输入指令，自动生成并执行测试</p>",
    unsafe_allow_html=True)

# 0. 初始化全局状态
if "init_cleanup" not in st.session_state:
    if os.path.exists("test_result.png"):
        os.remove("test_result.png")
    st.session_state.init_cleanup = True

if "current_code" not in st.session_state:
    st.session_state.current_code = None
if "messages" not in st.session_state:
    st.session_state.messages = [{"role": "assistant", "content": "👋 身份验证通过！请描述你想测试的场景。"}]
if "run_logs" not in st.session_state:
    st.session_state.run_logs = ""
if "iteration_count" not in st.session_state:
    st.session_state.iteration_count = 0
if "base_filename" not in st.session_state:
    st.session_state.base_filename = "test_case"
if "last_status" not in st.session_state:
    st.session_state.last_status = None

# 侧边栏配置
with st.sidebar:
    st.header("⚙️ 系统配置")
    api_key = st.text_input("DeepSeek API Key", type="password", value="sk-aa8adccb2a39412bab81e3f0e21c3a99")
    base_url = st.text_input("Base URL", value="https://api.deepseek.com/v1")
    st.divider()
    if st.button("退出登录"):
        st.session_state.authenticated = False
        st.rerun()
    st.divider()
    st.markdown("### 系统设置")
    show_browser = st.checkbox("运行测试时显示浏览器", value=True)
    max_retries = st.slider("最大自动纠错次数", min_value=1, max_value=5, value=3, step=1)

# 后续代码保持不变...
# (这里继续你之前的 Prompt 引擎、布局逻辑和核心流转逻辑)
# ==========================================
# 1. 大模型 Prompt 核心引擎
# ==========================================
BASE_RULES = """
【全局绝对规则】：
1. 必须是一个合法的 pytest 测试文件。测试函数以 `test_` 开头并使用 `page` fixture。
2. 只输出纯净 Python 代码，不要任何解释。
3. 【自主推断】：优先使用 page.get_by_placeholder(), get_by_role() 等语义定位器。
4. 【防坑指南】：
   - 绝对禁止使用 `page.wait_for_load_state('networkidle')`！请用 `wait_for_load_state('load')` 代替。
   - 必须用 evaluate 强行聚焦后，再用 keyboard.type(text, delay=100) 模拟物理输入！
   - 输入完文本后，必须加一句 `page.wait_for_timeout(1000)`，然后再执行 `page.keyboard.press("Enter")`。
5. 必须包含截图逻辑，保存为 'test_result.png'。
6. 末尾必须保留自启动逻辑：
   if __name__ == '__main__':
       import pytest, sys
       pytest.main(["-v", "-s", "--headed", __file__])
"""


def extract_code(raw_response):
    match = re.search(r'```python\s*(.*?)\s*```', raw_response, re.DOTALL)
    if match: return match.group(1)
    return raw_response.replace("```", "").strip()


def classify_intent(instruction, current_code, client):
    if not current_code: return "NEW"
    sys_prompt = "你是一个意图识别引擎。判断指令是'完全抛弃当前代码写新测试(NEW)'，还是'基于当前代码修改(MODIFY)'？只输出 NEW 或 MODIFY。"
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "system", "content": sys_prompt},
                  {"role": "user", "content": f"当前代码：\n{current_code}\n\n指令：{instruction}"}],
        temperature=0.1
    )
    return "NEW" if "NEW" in resp.choices[0].message.content.upper() else "MODIFY"


def generate_filename(instruction, client):
    sys_prompt = "将自然语言测试指令，翻译成简短的英文 snake_case 文件名。以 test_ 开头，不带后缀，无废话。"
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": instruction}],
        temperature=0.1
    )
    name = re.sub(r'[^\w\s-]', '', resp.choices[0].message.content.strip())
    name = re.sub(r'[-\s]+', '_', name).lower()
    return name if name.startswith("test_") else "test_" + name


def generate_initial_code(instruction, client):
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "system", "content": "从头生成全新 pytest 代码。\n" + BASE_RULES},
                  {"role": "user", "content": f"创建指令：{instruction}"}],
        temperature=0.1
    )
    return extract_code(resp.choices[0].message.content)


def modify_existing_code(current_code, mod_instruction, client):
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "system", "content": "在现有代码上精准修改。不删减核心逻辑！\n" + BASE_RULES},
                  {"role": "user", "content": f"【代码】\n{current_code}\n【修改要求】\n{mod_instruction}"}],
        temperature=0.1
    )
    return extract_code(resp.choices[0].message.content)


def heal_failed_code(current_code, error_feedback, client):
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "system", "content": "分析日志修复错误。\n" + BASE_RULES},
                  {"role": "user", "content": f"【代码】\n{current_code}\n【报错】\n{error_feedback}"}],
        temperature=0.1
    )
    return extract_code(resp.choices[0].message.content)


# ==========================================
# 🌟 2. 极简分屏 UI 布局
# ==========================================
col_left, col_right = st.columns([4, 6], gap="large")

with col_left:
    st.markdown("### 💬 对话")
    chat_container = st.container(height=480, border=True)
    with chat_container:
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"], unsafe_allow_html=True)

    user_input = st.chat_input("请输入测试指令（例如：打开百度搜索“软件测试”）")

with col_right:
    st.markdown("### 📊 测试结果")
    work_container = st.container(height=560, border=True)

    with work_container:
        if st.session_state.last_status == "success":
            st.success("✅ 最近一次执行成功")
        elif st.session_state.last_status == "error":
            st.error("❌ 最近一次执行失败")

        tab_img, tab_code, tab_log = st.tabs(["🖼️ 测试结果", "📄 代码", "📺 日志"])

        with tab_img:
            if os.path.exists("test_result.png"):
                st.image("test_result.png", use_container_width=True)
            else:
                st.info("暂无截图")

        with tab_code:
            if st.session_state.current_code:
                st.code(st.session_state.current_code, language="python", line_numbers=True)
            else:
                st.info("暂无代码")

        with tab_log:
            if st.session_state.run_logs:
                st.code(st.session_state.run_logs, language="bash")
            else:
                st.info("暂无日志")

# ==========================================
# 3. 核心流转逻辑
# ==========================================
if user_input:
    client = OpenAI(api_key=api_key, base_url=base_url)
    st.session_state.messages.append({"role": "user", "content": user_input})
    with chat_container:
        with st.chat_message("user"):
            st.markdown(user_input)

    with chat_container:
        with st.chat_message("assistant"):
            with st.status("🚀 正在执行流水线...", expanded=True) as status:
                intent = classify_intent(user_input, st.session_state.current_code, client)
                if intent == "NEW":
                    st.session_state.current_code = None
                    st.session_state.iteration_count = 0
                    st.session_state.base_filename = generate_filename(user_input, client)
                    new_code = generate_initial_code(user_input, client)
                else:
                    new_code = modify_existing_code(st.session_state.current_code, user_input, client)

                current_code = new_code
                error_feedback = None
                is_success = False
                full_log = ""
                trace_html = "<details><summary>🕵️ 点击查看内部执行过程</summary>\n\n"

                if os.path.exists("test_result.png"): os.remove("test_result.png")

                for attempt in range(1, max_retries + 1):
                    if attempt > 1: current_code = heal_failed_code(current_code, error_feedback, client)
                    with open("test_generated_script.py", "w", encoding="utf-8") as f:
                        f.write(current_code)
                    pytest_args = [sys.executable, "-m", "pytest", "test_generated_script.py", "-v", "--color=no",
                                   "--tb=short"]
                    if show_browser: pytest_args.append("--headed")
                    result = subprocess.run(pytest_args, capture_output=True, text=True, encoding="utf-8",
                                            errors="replace")
                    full_log = (result.stdout + result.stderr).strip()
                    if result.returncode == 0:
                        is_success = True
                        break
                    else:
                        error_feedback = full_log

                st.session_state.run_logs = full_log
                if is_success:
                    st.session_state.last_status = "success"
                    st.session_state.iteration_count += 1
                    st.session_state.current_code = current_code
                    save_dir = "successful_tests";
                    os.makedirs(save_dir, exist_ok=True)
                    timestamp = datetime.now().strftime("%y%m%d_%H%M%S")
                    saved_file_name = f"{st.session_state.base_filename}_v{st.session_state.iteration_count}_{timestamp}.py"
                    with open(os.path.join(save_dir, saved_file_name), "w", encoding="utf-8") as f_save:
                        f_save.write(current_code)
                    response_text = f"✅ 执行成功！代码已更新至 **V{st.session_state.iteration_count}**。"
                else:
                    st.session_state.last_status = "error"
                    response_text = "❌ 执行失败。"

                st.markdown(response_text)
                st.session_state.messages.append({"role": "assistant", "content": response_text})
    st.rerun()