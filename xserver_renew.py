#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XServer 游戏服自动延期 - SeleniumBase UC 版
解决登录页 CF Turnstile 人机验证问题（Playwright 无头会被拦）
沿用 cf-login-keepalive 技能的 UC 模式 + uc_gui_click_captcha 方案
"""
import os
import sys
import time
import re
import json
import platform
import logging
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional

from seleniumbase import SB
from seleniumbase.common.exceptions import TimeoutException, NoSuchElementException

# ================== 配置 ==================
LOGIN_URL = "https://secure.xserver.ne.jp/xapanel/login/xmgame"
OUTPUT_DIR = Path("output/screenshots")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("xserver-renew")


# ================== 辅助函数 ==================
def is_linux() -> bool:
    return platform.system().lower() == "linux"


def mask_email(email: str) -> str:
    if "@" not in email:
        return email[:1] + "***"
    local, domain = email.split("@", 1)
    masked_local = local[:1] + "***" if local else "***"
    if "." in domain:
        parts = domain.split(".")
        tld = parts[-1]
        first_char = domain[0]
        masked_domain = f"{first_char}***.{tld}" if len(parts) > 1 else f"{first_char}***"
    else:
        masked_domain = domain[:1] + "***"
    return f"{masked_local}@{masked_domain}"


def setup_display():
    if is_linux() and not os.environ.get("DISPLAY"):
        try:
            from pyvirtualdisplay import Display
            display = Display(visible=False, size=(1920, 1080))
            display.start()
            os.environ["DISPLAY"] = display.new_display_var
            logger.info("虚拟显示已启动")
            return display
        except Exception as e:
            logger.error(f"虚拟显示启动失败: {e}")
            sys.exit(1)
    return None


def screenshot_path(name: str) -> str:
    return str(OUTPUT_DIR / f"{datetime.now().strftime('%H%M%S')}-{name}.png")


def safe_screenshot(sb, path: str):
    try:
        sb.save_screenshot(path)
        logger.info(f"📸 截图 → {Path(path).name}")
    except Exception as e:
        logger.warning(f"截图失败: {e}")


def notify_telegram(account: str, ok: bool, msg: str = "", screenshot_file: str = None):
    try:
        token = os.environ.get("TG_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TG_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return

        status = "✅ XServer 续期成功" if ok else "❌ XServer 续期失败"
        lines = [status, "", f"账号：{mask_email(account)}"]
        if msg:
            lines.append(f"信息：{msg}")
        lines.append(f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        lines.append("XServer Auto Renew")
        text = "\n".join(lines)

        if screenshot_file and Path(screenshot_file).exists():
            with open(screenshot_file, "rb") as f:
                r = requests.post(
                    f"https://api.telegram.org/bot{token}/sendPhoto",
                    data={"chat_id": chat_id, "caption": text},
                    files={"photo": f},
                    timeout=60,
                )
                if r.status_code != 200:
                    logger.warning(f"TG 图片发送失败: {r.text[:150]}")
        else:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
                timeout=30,
            )
    except Exception as e:
        logger.warning(f"Telegram 通知失败: {e}")


# ================== Cloudflare / Turnstile 处理 ==================
def is_turnstile_present(sb) -> bool:
    """登录页里的 Turnstile 挂件是否在当前页面"""
    try:
        return sb.execute_script('''
            return !!(document.querySelector('input[name="cf-turnstile-response"]')
                   || document.querySelector('iframe[src*="challenges.cloudflare.com"]')
                   || document.querySelector('iframe[src*="cf-turnstile"]'));
        ''')
    except:
        return False


def is_cloudflare_interstitial(sb) -> bool:
    """整页 CF 挑战（Just a moment / Verify you are human）"""
    try:
        page_source = sb.get_page_source()
        title = sb.get_title().lower() if sb.get_title() else ""
        for indicator in ["Just a moment", "Verify you are human", "Checking your browser",
                          "Checking if the site connection is secure"]:
            if indicator in page_source:
                return True
        if "just a moment" in title or "attention required" in title:
            return True
        # 页面极短 + CF 域名引用
        try:
            body_len = sb.execute_script("return (document.body && document.body.innerText) ? document.body.innerText.trim().length : 0")
            if body_len < 100 and "challenges.cloudflare.com" in page_source:
                return True
        except:
            pass
        return False
    except:
        return False


def bypass_cloudflare_interstitial(sb, max_attempts: int = 3) -> bool:
    logger.info("检测到 CF 整页挑战，尝试绕过...")
    for attempt in range(max_attempts):
        try:
            sb.uc_gui_click_captcha()
            time.sleep(6)
            if not is_cloudflare_interstitial(sb):
                logger.info("✅ CF 整页挑战已通过")
                return True
        except Exception as e:
            logger.warning(f"CF 整页绕过 {attempt+1} 失败: {e}")
        time.sleep(3)
    return False


def wait_for_turnstile_success(sb, timeout: int = 25) -> bool:
    logger.info("等待 Turnstile 验证...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            ok = sb.execute_script('''
                var resp = document.querySelector('input[name="cf-turnstile-response"]');
                if (resp && resp.value && resp.value.length > 20) return true;
                var iframe = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
                if (iframe && iframe.getAttribute("data-state") === "solved") return true;
                return false;
            ''')
            if ok:
                logger.info("✅ Turnstile 验证成功")
                return True
        except:
            pass
        time.sleep(1)
    logger.warning("⏰ Turnstile 验证超时")
    return False


def solve_turnstile(sb) -> bool:
    """优先等自动完成; 否则用物理鼠标点验证框"""
    if wait_for_turnstile_success(sb, timeout=6):
        return True
    for attempt in range(3):
        try:
            sb.uc_gui_click_captcha()
            logger.info(f"UC 点击验证码第 {attempt+1} 次")
        except Exception as e:
            logger.warning(f"UC 点击异常: {e}")
        time.sleep(2)
        if wait_for_turnstile_success(sb, timeout=10):
            return True
    logger.warning("Turnstile 多次尝试后未显式通过，尝试直接提交")
    return False


# ================== XServer 登录 ==================
def xserver_login(sb, account: str, password: str) -> bool:
    """返回是否登录成功(已进入面板)"""
    logger.info("访问 XServer 登录页...")
    sb.uc_open_with_reconnect(LOGIN_URL, reconnect_time=8)
    time.sleep(4)

    if is_cloudflare_interstitial(sb):
        logger.info("检测到 CF 整页挑战，先处理")
        bypass_cloudflare_interstitial(sb)
        time.sleep(3)

    # 登录表单字段
    id_filled = False
    id_selectors = [
        'input#xserver_user_login',
        'input#user_login',
        'input[name="xuserID"]',
        'input[name="loginid"]',
        'input[type="text"]',
    ]
    for sel in id_selectors:
        try:
            sb.wait_for_element_visible(sel, timeout=4)
            sb.clear(sel)
            sb.type(sel, account)
            id_filled = True
            logger.info(f"✅ 填写账号ID ({sel})")
            break
        except:
            continue

    pw_filled = False
    for sel in ['input#user_password', 'input[name="xpassword"]', 'input[name="password"]', 'input[type="password"]']:
        try:
            sb.wait_for_element_visible(sel, timeout=4)
            sb.clear(sel)
            sb.type(sel, password)
            pw_filled = True
            logger.info(f"✅ 填写密码 ({sel})")
            break
        except:
            continue

    if not id_filled or not pw_filled:
        sp = screenshot_path("01-no-form")
        safe_screenshot(sb, sp)
        logger.error("未找到登录表单字段")
        return False

    if is_turnstile_present(sb):
        solve_turnstile(sb)
    else:
        logger.info("当前页面无 Turnstile 挂件")

    sp = screenshot_path("02-before-submit")
    safe_screenshot(sb, sp)

    # 提交登录
    submitted = False
    for sel in ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("ログインする")', 'a:has-text("ログインする")']:
        try:
            sb.click(sel)
            submitted = True
            break
        except:
            continue
    if not submitted:
        try:
            sb.execute_script('document.querySelector("form").submit()')
            submitted = True
        except:
            pass
    if not submitted:
        logger.error("登录提交失败")
        return False

    logger.info("等待登录完成...")
    time.sleep(6)

    current_url = sb.get_current_url()
    logger.info(f"登录后 URL: {current_url}")

    found_manage = False
    for sel in ['a:has-text("ゲーム管理")', 'link=ゲーム管理', 'a[href*="xmgame"]']:
        try:
            sb.wait_for_element_visible(sel, timeout=8)
            found_manage = True
            break
        except:
            continue

    if not found_manage and "/login" in current_url:
        sp = screenshot_path("03-login-failed")
        safe_screenshot(sb, sp)
        logger.error("仍停留在登录页，登录失败")
        return False

    if not found_manage and is_turnstile_present(sb):
        # 可能 Turnstile 没通过
        logger.info("登录后未进面板, 再处理一次 Turnstile")
        solve_turnstile(sb)
        time.sleep(3)
        for sel in ['a:has-text("ゲーム管理")', 'link=ゲーム管理']:
            try:
                sb.wait_for_element_visible(sel, timeout=8)
                found_manage = True
                break
            except:
                continue

    return found_manage


# ================== 标签页处理 ==================
def switch_to_new_tab(sb) -> bool:
    """若点击后开了新标签, 切换过去"""
    try:
        handles = sb.driver.window_handles
        if len(handles) > 1:
            sb.driver.switch_to.window(handles[-1])
            logger.info(f"已切换到新标签 (共 {len(handles)} 个窗口)")
            time.sleep(2)
            return True
    except:
        pass
    return False


def click_nav(sb, text: str, timeout: int = 8) -> bool:
    """点击导航链接(优先精准文本匹配, 支持自动切新标签)。返回是否成功"""
    url_before = sb.get_current_url()
    try:
        sb.wait_for_element_visible(f'a:text("{text}")', timeout=timeout)
        sb.click(f'a:text("{text}")')
        time.sleep(3)
        # 检查是否新开标签
        if switch_to_new_tab(sb):
            return True
        # 检查 URL 是否变化 (导航成功)
        if sb.get_current_url() != url_before:
            return True
        return True
    except:
        pass
    # 兜底: JS 文本匹配点击
    try:
        ok = sb.execute_script('''
            var text = arguments[0];
            var nodes = document.querySelectorAll('a, button, [role="button"]');
            for (var i = nodes.length - 1; i >= 0; i--) {
                var n = nodes[i];
                var t = (n.textContent || '').trim();
                if (t.indexOf(text) !== -1) {
                    n.click();
                    return true;
                }
            }
            return false;
        ''', text)
        if ok:
            time.sleep(3)
            switch_to_new_tab(sb)
            return True
    except Exception as e:
        logger.warning(f"click_nav 兜底失败: {e}")
    return False


def click_button_text(sb, text: str, timeout: int = 8) -> bool:
    for sel in [f'button:text("{text}")', f'a:text("{text}")', f'input[value="{text}"]']:
        try:
            sb.wait_for_element_visible(sel, timeout=timeout)
            sb.click(sel)
            return True
        except:
            continue
    try:
        ok = sb.execute_script('''
            var text = arguments[0];
            var nodes = document.querySelectorAll('button, a, input');
            for (var i = nodes.length - 1; i >= 0; i--) {
                var n = nodes[i];
                var t = (n.textContent || n.value || '').trim();
                if (t.indexOf(text) !== -1) { n.click(); return true; }
            }
            return false;
        ''', text)
        if ok:
            return True
    except Exception as e:
        logger.warning(f"click_button_text 失败: {e}")
    return False


# ================== 延续流程 ==================
def xserver_extend(sb, account: str) -> tuple:
    """执行延期, 返回 (ok, message, screenshot_path)"""
    sp = screenshot_path("04-managed")
    safe_screenshot(sb, sp)

    # 进入游戏管理
    if not click_nav(sb, "ゲーム管理", timeout=10):
        # 再试一次 (可能列表页需要点服务器卡片)
        try:
            sb.execute_script('document.querySelector(\'a[href*="xmgame"]\').click()')
            time.sleep(3)
            switch_to_new_tab(sb)
        except Exception as e:
            return False, f"点击ゲーム管理失败: {e}", sp
    time.sleep(4)

    sp2 = screenshot_path("05-game-panel")
    safe_screenshot(sb, sp2)

    # 升级/延期入口
    clicked_upg = False
    for sel in ['a:has-text("アップグレード・期限延長")', 'link=アップグレード・期限延長']:
        try:
            sb.wait_for_element_visible(sel, timeout=8)
            sb.click(sel)
            clicked_upg = True
            break
        except:
            continue
    if not clicked_upg:
        # 兜底 JS 点击
        clicked_upg = click_button_text(sb, "アップグレード・期限延長", timeout=5)
    if not clicked_upg:
        return False, "未找到 アップグレード・期限延長 入口", sp2
    time.sleep(3)
    switch_to_new_tab(sb)
    time.sleep(2)

    sp3 = screenshot_path("06-upgrade-page")
    safe_screenshot(sb, sp3)

    # 期限を延長する (入口)
    if not click_button_text(sb, "期限を延長する", timeout=8):
        # 检查下次可更新时间
        try:
            body_text = sb.execute_script("return document.body ? document.body.innerText : ''")
        except:
            body_text = ""
        m = re.search(r"更新をご希望の場合は、(.+?)以降にお試しください。", body_text, re.S)
        if m:
            msg = f"暂时无法延期，下次可尝试: {m.group(1).strip()}"
            logger.warning(msg)
            return True, msg, sp3
        msg = "未找到 期限を延長する 入口"
        return False, msg, sp3
    time.sleep(3)
    switch_to_new_tab(sb)

    sp4 = screenshot_path("07-extend-page")
    safe_screenshot(sb, sp4)

    # 确认画面
    click_button_text(sb, "確認画面に進む", timeout=6)
    time.sleep(3)

    # 最终延长按钮
    if not click_button_text(sb, "期限を延長する", timeout=8):
        sp5 = screenshot_path("08-no-final")
        safe_screenshot(sb, sp5)
        return False, "未找到最终延长按钮", sp5
    time.sleep(4)
    sp5 = screenshot_path("08-done")
    safe_screenshot(sb, sp5)
    return True, "续期完成", sp5


# ================== 主函数 ==================
def main():
    try:
        raw = os.environ.get("USERS_JSON", "")
        if not raw:
            logger.error("未设置 USERS_JSON")
            sys.exit(1)
        users = json.loads(raw)
        if not isinstance(users, list) or len(users) == 0:
            logger.error("USERS_JSON 必须是非空数组")
            sys.exit(1)
    except Exception as e:
        logger.error(f"解析 USERS_JSON 失败: {e}")
        sys.exit(1)

    proxy = os.environ.get("PROXY_SERVER")
    display = setup_display()

    all_ok = True
    try:
        for user in users:
            account = user.get("username", "")
            password = user.get("password", "")
            logger.info("=" * 50)
            logger.info(f"处理账号: {mask_email(account)}")
            logger.info("=" * 50)

            try:
                sb_kwargs = dict(
                    uc=True,
                    test=True,
                    locale="ja",
                    headed=not is_linux(),
                    user_data_dir=None,
                    chromium_arg="--disable-blink-features=AutomationControlled",
                )
                if proxy:
                    sb_kwargs["proxy"] = proxy

                with SB(**sb_kwargs) as sb:
                    logged_in = xserver_login(sb, account, password)
                    if not logged_in:
                        raise RuntimeError("登录失败")
                    ok, msg, sp = xserver_extend(sb, account)
                    notify_telegram(account, ok, msg, sp)
                    if not ok:
                        all_ok = False
            except Exception as e:
                logger.exception(f"账号 {mask_email(account)} 处理异常")
                notify_telegram(account, False, str(e))
                all_ok = False

        if all_ok:
            logger.info("✅ 全部账号处理完成")
            sys.exit(0)
        else:
            logger.error("❌ 存在失败的账号")
            sys.exit(1)
    finally:
        if display:
            display.stop()


if __name__ == "__main__":
    main()
