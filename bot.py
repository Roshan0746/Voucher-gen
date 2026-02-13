import logging
import asyncio
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from playwright.async_api import async_playwright

# --- CONFIGURATION ---
BOT_TOKEN = '8044855668:AAG1zK4QzkXqDzcYV9KvHbdAk3c3VdYQHKk'  # <--- REPLACE THIS
WEBSITE_URL = 'https://sheincodes.shop/'

# Setup Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- BROWSER AUTOMATION ENGINE ---
async def run_website_task(cookie, action_type):
    """
    Spawns a headless browser, goes to the site, inputs the cookie,
    clicks the requested button, and scrapes the result.
    """
    async with async_playwright() as p:
        # Launch browser (headless=True means invisible)
        browser = await p.chromium.launch(headless=True)
        
        # Create a context that looks like a real mobile user (to match site responsiveness)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1",
            viewport={'width': 375, 'height': 812}
        )
        page = await context.new_page()

        try:
            # 1. Navigate to site
            await page.goto(WEBSITE_URL, wait_until='networkidle', timeout=60000)

            # 2. Input the Cookie
            await page.wait_for_selector('#cookieInput')
            await page.fill('#cookieInput', cookie)

            # 3. Perform the specific action requested by the user
            if action_type == 'generate':
                # Click the purple "GENERATE" button
                await page.click('.btn-generate')
            elif action_type == 'recover':
                # Click the transparent "RECOVER" button
                await page.click('.btn-recover')

            # 4. Wait for the Result Modal
            # The site shows #resultModal with display:flex when done
            try:
                await page.wait_for_function(
                    "document.getElementById('resultModal').style.display === 'flex'",
                    timeout=60000
                )
            except Exception:
                return {"status": "error", "message": "⏱️ Timeout: The website took too long to respond."}

            # 5. Scrape the Results
            # We run this JS inside the browser to analyze what popped up
            result = await page.evaluate(f"""() => {{
                // Check for Success Elements
                const couponCode = document.querySelector('.coupon-code');
                const couponAmount = document.querySelector('.coupon-amount');
                
                // Check for Recovery specific elements (list of coupons)
                const recoveryHeader = document.querySelector('.result-header h2');
                
                if (couponCode) {{
                    // GENERATE SUCCESS
                    return {{
                        status: 'success',
                        type: 'generate',
                        code: couponCode.innerText.trim(),
                        amount: couponAmount ? couponAmount.innerText.trim() : 'N/A'
                    }};
                }} 
                else if (recoveryHeader && recoveryHeader.innerText.includes('COUPON')) {{
                    // RECOVERY SUCCESS (List of coupons)
                    const items = [];
                    document.querySelectorAll('.info-box').forEach(box => {{
                        const code = box.querySelector('strong')?.innerText;
                        const details = box.querySelector('div:nth-child(2)')?.innerText;
                        if(code) items.push({{code, details}});
                    }});
                    return {{
                        status: 'success',
                        type: 'recover',
                        count: items.length,
                        items: items
                    }};
                }}
                else {{
                    // FAILURE / ERROR MESSAGE
                    const errorMsg = document.querySelector('.result-header p')?.innerText 
                                  || document.querySelector('.result-body p')?.innerText 
                                  || 'Unknown error on website.';
                    return {{
                        status: 'fail',
                        message: errorMsg
                    }};
                }}
            }}""")
            
            return result

        except Exception as e:
            return {"status": "error", "message": f"System Error: {str(e)}"}
        finally:
            await browser.close()

# --- TELEGRAM BOT HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point: Welcomes the user."""
    await update.message.reply_text(
        "💎 **Welcome to Shein Coupon Bot** 💎\n\n"
        "To get started, please **paste your Instagram Cookie** below.\n"
        "_(I will hold onto it until you choose an action)_",
        parse_mode='Markdown'
    )

async def receive_cookie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles text messages (assumed to be cookies)."""
    user_cookie = update.message.text.strip()

    # Basic validation
    if len(user_cookie) < 20:
        await update.message.reply_text("⚠️ That doesn't look like a valid cookie. Try again.")
        return

    # Save cookie in user_data memory
    context.user_data['cookie'] = user_cookie

    # Show Action Keyboard
    keyboard = [
        [
            InlineKeyboardButton("⚡ Generate New", callback_data='generate'),
            InlineKeyboardButton("♻️ Recover Old", callback_data='recover'),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🍪 **Cookie Received!**\n\n"
        "What would you like to do on the website?",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the button clicks."""
    query = update.callback_query
    action = query.data
    await query.answer() # Stop loading animation on button

    # Retrieve cookie
    user_cookie = context.user_data.get('cookie')
    if not user_cookie:
        await query.edit_message_text("⚠️ Session expired. Please send your cookie again.")
        return

    # UI Feedback
    if action == 'generate':
        await query.edit_message_text(f"⚡ **Generating Coupon...**\nConnecting to {WEBSITE_URL}...\nPlease wait ~10-20 seconds.", parse_mode='Markdown')
    else:
        await query.edit_message_text(f"♻️ **Recovering Coupons...**\nChecking database...\nPlease wait ~10-20 seconds.", parse_mode='Markdown')

    # Run the Browser Task
    result = await run_website_task(user_cookie, action)

    # Format the Output
    if result['status'] == 'success':
        if result.get('type') == 'generate':
            # Single Coupon Result
            msg = (
                f"✅ **SUCCESSFULLY GENERATED**\n\n"
                f"💰 **Value:** {result['amount']}\n"
                f"🎟 **Code:** `{result['code']}`\n\n"
                f"_Tap code to copy_"
            )
        else:
            # Recovery Result (List)
            msg = f"♻️ **RECOVERY SUCCESSFUL**\nFound {result['count']} coupons:\n\n"
            for item in result['items']:
                msg += f"🎟 `{item['code']}`\n   └ {item['details']}\n"
    elif result['status'] == 'fail':
        msg = f"❌ **WEBSITE ERROR**\n\nThe website said:\n_{result['message']}_"
    else:
        msg = f"⚠️ **BOT ERROR**\n\n{result['message']}"

    # Send Final Result
    await query.message.reply_text(msg, parse_mode='Markdown')
    
    # Offer to go again
    await query.message.reply_text("Send a new cookie to start over.")

# --- MAIN EXECUTION ---
if __name__ == '__main__':
    # Initialize Bot
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Add Handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_cookie))

    print("Bot is running...")
    app.run_polling()
  
