const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

// Telegram API 配置
const TG_BOT_TOKEN = process.env.TG_BOT_TOKEN;
const TG_CHAT_ID = process.env.TG_CHAT_ID;

/**
 * 发送 Telegram 通知 (支持图片)
 */
async function sendTelegramNotification(message, imagePath = null) {
    if (!TG_BOT_TOKEN || !TG_CHAT_ID) {
        console.log('未设置 Telegram Bot Token 或 Chat ID，跳过通知。');
        return;
    }

    try {
        if (imagePath) {
            // 发送带图片的通知
            const formData = new FormData();
            formData.append('chat_id', TG_CHAT_ID);
            formData.append('caption', message);

            const fileBuffer = fs.readFileSync(imagePath);
            const blob = new Blob([fileBuffer]);
            formData.append('photo', blob, path.basename(imagePath));

            const response = await fetch(`https://api.telegram.org/bot${TG_BOT_TOKEN}/sendPhoto`, {
                method: 'POST',
                body: formData
            });

            if (!response.ok) {
                console.error('Telegram 图片发送失败:', await response.text());
            } else {
                console.log('Telegram 通知(含图片)已发送');
            }
        } else {
            // 仅发送文字通知
            const response = await fetch(`https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    chat_id: TG_CHAT_ID,
                    text: message
                })
            });

            if (!response.ok) {
                console.error('Telegram 消息发送失败:', await response.text());
            } else {
                console.log('Telegram 文字通知已发送');
            }
        }
    } catch (error) {
        console.error('发送 Telegram 通知时出错:', error);
    }
}

(async () => {
    // 从环境变量读取用户
    let users = [];
    try {
        if (process.env.USERS_JSON) {
            users = JSON.parse(process.env.USERS_JSON);
            if (!Array.isArray(users)) {
                console.error('USERS_JSON 必须是对象数组。');
                process.exit(1);
            }
        } else {
            console.log('未找到 USERS_JSON 环境变量。');
            process.exit(1);
        }
    } catch (err) {
        console.error('解析 USERS_JSON 出错:', err);
        process.exit(1);
    }

    const browser = await chromium.launch({
        headless: true,
        channel: 'chrome',
    });

    for (const user of users) {
        console.log(`正在处理用户: ${user.username}`);
        const context = await browser.newContext();
        const page = await context.newPage();

        try {
            // 1. 导航到登录页面
            await page.goto('https://secure.xserver.ne.jp/xapanel/login/xmgame');

            // 2. 登录
            await page.getByRole('textbox', { name: 'XServerアカウントID または メールアドレス' }).click();
            await page.getByRole('textbox', { name: 'XServerアカウントID または メールアドレス' }).fill(user.username);
            await page.locator('#user_password').fill(user.password);
            await page.getByRole('button', { name: 'ログインする' }).first().click();

            // 等待导航
            await page.getByRole('link', { name: 'ゲーム管理' }).click();
            await page.waitForLoadState('networkidle');

            // 3. 升级 / 延长
            await page.getByRole('link', { name: 'アップグレード・期限延長' }).click();

            // 4. 选择 '延长期间' - 检查是否可用
            try {
                await page.getByRole('link', { name: '期限を延長する' }).waitFor({ state: 'visible', timeout: 5000 });
                await page.getByRole('link', { name: '期限を延長する' }).click();
            } catch (e) {
                // 检查是否有具体的下一次更新时间提示
                const bodyText = await page.locator('body').innerText();
                const match = bodyText.match(/更新をご希望の場合は、(.+?)以降にお試しください。/);

                let msg;
                if (match && match[1]) {
                    msg = `⚠️ 用户 ${user.username} 目前无法延期，下次延长时间在：${match[1]}`;
                } else {
                    msg = `⚠️ 用户 ${user.username} 未找到 '期限延长' 按钮。可能无法延长。`;
                }

                console.log(msg);
                // 保存截图
                const screenshotPath = `skip_${user.username}.png`;
                await page.screenshot({ path: screenshotPath });
                await sendTelegramNotification(msg, screenshotPath);
                continue;
            }

            // 5. 确认
            await page.getByRole('button', { name: '確認画面に進む' }).click();

            // 6. 执行延长
            console.log(`正在点击用户 ${user.username} 的最终延长按钮...`);
            await page.getByRole('button', { name: '期限を延長する' }).click();

            // 7. 返回
            await page.getByRole('link', { name: '戻る' }).click();

            const successMsg = `✅ 用户 ${user.username} 成功延长期限`;
            console.log(successMsg);
            const successPath = `success_${user.username}.png`;
            await page.screenshot({ path: successPath });
            await sendTelegramNotification(successMsg, successPath);

        } catch (error) {
            const errorMsg = `❌ 用户 ${user.username} 处理失败: ${error}`;
            console.error(errorMsg);
            const errorPath = `error_${user.username}.png`;
            await page.screenshot({ path: errorPath });
            await sendTelegramNotification(errorMsg, errorPath);
            // 不退出进程，继续下一个用户
        } finally {
            await context.close();
        }
    }

    await browser.close();
})();
