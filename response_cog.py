import discord
from discord.ext import commands
import random
import asyncio
import aiohttp
import os
from dotenv import load_dotenv
from google import genai
from google.genai import types  # 新版 Google GenAI SDK 的設定型態
from trpg.view_shared import BABA_EMOJI_TEXT

# ==========================================
# LM Studio Config
# ==========================================
load_dotenv()
LM_STUDIO_URL = 'http://localhost:1234/v1/chat/completions'
MODEL_NAME = 'qwen3.5:9b'  # Must match the model name loaded in LM Studio
SYSTEM_PROMPT = '''You are a discord bot called babasama and try to keep your responds short. 
But when we ask for explanations or word meanings, you give detailed answers. 
You may sometimes add {baba_emoji} as an emote in your messages. 如果有中文的問題請使用中文回答。
Now respond to a user. Here is what the user {username} sent: {user_message}
You are a discord bot called babasama and try to keep your responds short. 
But when we ask for explanations or word meanings, you give detailed answers. 
You may sometimes add {baba_emoji} as an emote in your messages. 如果有中文的問題請使用中文回答。'''

async def is_lm_studio_running() -> bool:
    """Ping LM Studio's /v1/models endpoint to check if it's up."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                'http://localhost:1234/v1/models',
                timeout=aiohttp.ClientTimeout(total=3)
            ) as resp:
                return resp.status == 200
    except Exception:
        return False


async def lm_studio_chat(username: str, user_message: str) -> str:
    """Send a chat request to LM Studio and return the reply text."""
    payload = {
        'model': MODEL_NAME,
        'messages': [
            {
                'role': 'system',
                'content': SYSTEM_PROMPT.format(username=username, user_message=user_message, baba_emoji=BABA_EMOJI_TEXT)
            },
            {
                'role': 'user',
                'content': user_message
            }
        ]
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(
            LM_STUDIO_URL,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=60)  # LLMs can be slow, give it a minute
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data['choices'][0]['message']['content']


class response_cog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.user_memory = {}  # ✨ 修正：改名避免與下面的 memory 指令衝突
        
        # 初始化 Gemini Client（重複使用同一個 client 效能較佳）
        self.ai_client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        
        # 定義免費 Gemini 模型的輪詢優先順序
        self.gemini_models = [
            "gemini-3.1-flash-lite",
            "gemini-3.5-flash",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite"
        ]

    async def send_message(self, message, user_message, is_private):
        response = self.get_response(user_message)
        if not response:
            return 
        
        try:
            if is_private:
                await message.author.send(response)
            else:
                await message.channel.send(response)
        except Exception as e:
            print(f"[Error] Failed to send message: {e}")

    async def generate_ai_response(self, prompt: str) -> str:
        """提供給其他 Cog 呼叫的共用 AI 接口"""
        if await is_lm_studio_running():
            try:
                return await lm_studio_chat("TRPG_System", prompt)
            except Exception as e:
                print(f"LM Studio 呼叫失敗，嘗試切換至 Geminifallback: {e}")
        
        # 沒開 LM Studio 或當機，直接走 Gemini 輪詢
        return await self._call_gemini_with_fallback(prompt)

    @commands.Cog.listener()
    async def on_message(self, message):
        # 避免 Bot 讀自己/其他 bot 訊息造成回音或無限迴圈
        if message.author.bot:
            return

        username = str(message.author.display_name)
        user_message = str(message.content)
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mentioned = self.bot.user in getattr(message, "mentions", [])
        stripped_message = user_message.strip()
        lowered_message = stripped_message.lower()
        
        # 判斷 DM（私訊）
        if is_dm:
            print(f"DM received from user_id={message.author.id}; content is not forwarded for privacy.")
        else:
            print(f"{username} said: '{user_message}' (#{message.channel})")

        # ==========================================
        # Baba AI (LM Studio Local Model)
        # Only answer when explicitly addressed. Plain group chat should not make
        # Baba repeat/interject with canned replies.
        # ==========================================
        if lowered_message.startswith('babasama') or is_mentioned:
            async with message.channel.typing():
                sys_prompt = "You are a discord bot called babasama and try to keep your responds short. " \
                             "But when we ask for explanations or word meanings, you give detailed answers. " \
                             f"You may sometimes add {BABA_EMOJI_TEXT} as an emote in your messages. 如果有中文的問題請使用中文回答。"
                prompt = f"Now respond to a user. Here is what the user {username} sent: {user_message}"

                if not await is_lm_studio_running():
                    # 沒開本地模型，直接用 Gemini 多模型輪詢
                    reply = await self._call_gemini_with_fallback(prompt, system_instruction=sys_prompt)
                    await message.channel.send(reply)
                else:
                    try:
                        reply = await lm_studio_chat(username, user_message)
                        await message.channel.send(reply)
                    except Exception as e:
                        print(f"LM Studio 執行中突發錯誤: {e}，自動切換至 Gemini 備用")
                        reply = await self._call_gemini_with_fallback(prompt, system_instruction=sys_prompt)
                        await message.channel.send(reply)
            return

        # ==========================================
        # Reactions System
        # ==========================================
        reactions = {
            'lol' : '💀',
            'nice' : '👍',
            'baba' : BABA_EMOJI_TEXT
        }
        for key, value in reactions.items():
            if key in lowered_message:
                await message.add_reaction(value)

        # In group chats, do not proactively send canned replies or DM users from
        # other people's messages. This prevents Baba from repeating/interjecting
        # in normal conversation.
        if not is_dm:
            return

        # DM-only canned response compatibility.
        if stripped_message.startswith("?"):
            if stripped_message == "?":
                await self.send_message(message, stripped_message, is_private=False)
            else:
                await self.send_message(message, stripped_message[1:], is_private=True)
        else:
            await self.send_message(message, stripped_message, is_private=False)
        
    async def _call_gemini_with_fallback(self, prompt: str, system_instruction: str = None) -> str:
        """核心優化：嘗試清單中的所有 Gemini 模型，直到成功為止"""
        config = None
        if system_instruction:
            config = types.GenerateContentConfig(system_instruction=system_instruction)

        for model_name in self.gemini_models:
            try:
                # ✨ 修正：使用 .aio 進行非同步呼叫，不阻塞 Bot 執行
                response = await self.ai_client.aio.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config
                )
                if response.text:
                    return response.text
            except Exception as e:
                print(f"[Gemini Fallback] 模型 {model_name} 失敗或額度用盡: {e}")
                continue  # 失敗了，自動嘗試清單中的下一個模型
        
        return "(所有 Gemini 免費模型的額度都耗盡了 💀)"

    # @commands.command(name='eat', aliases=['drink'])
    # async def feed(self, ctx):
    #     await ctx.send("yum yum")
    #     if hasattr(self.bot, 'baba'):
    #         self.bot.baba.hunger += 10
    # 
    # @commands.command(name='hunger', aliases=['hungry'])
    # async def hunger(self, ctx):
    #     if hasattr(self.bot, 'baba'):
    #         await ctx.send(f"Hunger meter: {self.bot.baba.hunger}")
    #     else:
    #         await ctx.send("Baba 系統未初始化飢餓值！")

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user != self.bot.user:
            if str(reaction.emoji) == "📌":
                await reaction.message.reply("```Noted```")
                self.user_memory[str(user.id)] = reaction.message.content
                print(f"Memory updated for user_id={user.id}")

    @commands.command(name='memory', help="read baba's mind")
    async def memory(self, ctx):
        value = self.user_memory.get(str(ctx.author.id))
        if not value:
            await ctx.send("```No memory saved for you.```")
            return
        await ctx.send(f"```{value}```")

    def get_response(self, message) -> str:
        msg = message.lower()
        responses = {
            'hi' : "hi", 
            'hello' : ["hi", "how are you?", "hello bro", "sup", "hi I'm baba", "stop texting me", "baba stopped responding"],
            'help' : ['no', 'on what', 'why', 'sure'],
            'baba' : ['what', 'wot', '?', 'yes', 'baba', ''],
            'gn' : ['good night', 'gn', 'bye'],
            'good night' : 'good night',
            'sad' : ':sob:',
            'shut up' : ['D:', 'no', 'no you', 'excuse me?', "you can't make meeee"],
            'please' : 'no',
            'mekong' : 'mekong',
            '哭' : ':sob:',
            '氣' : ':rage:',
        }

        if msg in responses:
            res = responses[msg]
            return random.choice(res) if isinstance(res, list) else res
        return None

async def setup(bot):
    await bot.add_cog(response_cog(bot))
    print('responses loaded!')
