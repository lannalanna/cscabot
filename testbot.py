#!/usr/bin/env python
import asyncio
import logging
import datetime
import json
from aiogram import Bot, Dispatcher, types
from aiogram.filters.command import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender


API_TOKEN = '8162784129:AAHbZZ1JZONUH8sujANe4txembuBeRsXaCM' 
API_TOKEN = '8211322326:AAFbYxJ-qI0ERUJOUygYSbOzAfXK-vjt0us'


topic_links = { 'parabola' : 'https://t.me/csca_math_exam/22',
                'trigonometry' : 'https://t.me/csca_math_exam/20',
                'hyperbola' : 'https://t.me/csca_math_exam/33',
                'sets' : 'https://t.me/csca_math_exam/10',
                'functions' : 'https://t.me/csca_math_exam/14',
                'geometry' : 'https://t.me/csca_math_exam/18',
                'sequences' : 'https://t.me/csca_math_exam/16',
                'inequalities' : 'https://t.me/csca_math_exam/12',
                'logarithnic functions' : 'https://t.me/csca_math_exam/24',
                'complex numbers': 'https://t.me/csca_math_exam/28',
                'physics' : 'https://t.me/csca_math_exam/55',
                'hyperbola' : 'https://t.me/csca_math_exam/33',
                'probability' : 'https://t.me/csca_math_exam/26'
                 }

stepik = set()
with open("/data/usr.txt", "r") as f:
      for l in f:
          s=l.split('\t')
           
          if len(s)>3 and 'stepik' in s[3]:
            stepik.add(s[1])


usrh={}
usrok={}
usrno={}
topics = []

kapibara = {}

for fname in ['data', 'data_jan'] :
 with open('/data/'+fname+'.txt', 'r') as f:  
   kpb = json.load(f) 
 
 for k in kpb :
    if 'topic' in k and k['topic'] not in topics : 
      topics = topics+[k['topic']] 


 for top in topics : 
   kapibara[top] = kapibara.get(top,[])+[x for x in kpb if x.get('topic','') == top] 
 kpb = {}

with open('/data/data_physics.txt', 'r') as f:  
  kpb = json.load(f) 

topics = topics+['physics'] 
for top in ['physics'] : 
 kapibara[top] = [x for x in kpb if x.get('topic','') == top] 

for top in topics :
   for k in kapibara[top] :
       long = [x for x in k['options'] if len(x)>45]
       if len(long) > 0 :
            k['long']="\n\n"+"\n".join(k['options'])
            k['options'] = ["A.","B.","C.","D."]



bot = Bot(token=API_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)
j=0

def log(usr, lg = []) :
   dt = datetime.datetime.now()
   id = usr.id  
   username = usr.username
   l = [dt, id, username] +  lg
   with open("/data/res.txt", "a") as f:
                   f.write("\t".join([str(x) for x in l])+"\n")

def makeinvite(usr) : 
       id = usr.id
       username = str(usr.username)
       inv = username[:3]+str(id)[:3]
       lang = usr.language_code 
       link = "https://t.me/csca_mathbot?start=invite"+inv
       if lang == "ru" :
                message = "CSCA math bot бесплатный. Чтобы продолжать пользоваться им неограниченно, отправьте ссылку-приглашения друзьям или опубликуйте ее в любом CSCA чате"
                message = message+"\n Персональная ссылка-приглашение "+link
                message = message +"\n Сейчас вы можете продолжать пользоваться ботом"
       else :    
                message = "CSCA math bot is free. To continue using it without limits, send an invitation link to friends or post it in any CSCA chat."
                message = message+"\n Your personal invitation link "+link
                message = message +"\n You can continue using the bot."
       return message

def start_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for top in topics :
       builder.add(
            InlineKeyboardButton(
                text=top[0].upper()+top[1:],
                callback_data=f'next_{top}_0'
            )
        )
    builder.adjust(1)
    return builder.as_markup()



def inline_kb(top, j : int, showvideo = 1) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    k = kapibara[top][j]["options"]
    # Добавляем кнопки вопросов
    correct_h={0:'A', 1:'B' , 2:'C' , 3: 'D', 4:'E'}
    for i in range(len(k)):
        builder.add(
            InlineKeyboardButton(
                text=k[i].replace('. ','.     '),
                callback_data=f'qst_{top}_{j}_{i}'
            )
        )
    if  showvideo and 'link' in kapibara[top][j] :
        builder.add(
            InlineKeyboardButton(
                text='[...link to the video...]',
                callback_data=f'explain_{top}_{j}'
            )
        )
   
  #  builder.row(
  #      InlineKeyboardButton(
  #          text='Получить подсказку',
  #          callback_data='explain'
  #      )
  #  )
    # Настраиваем размер клавиатуры
    builder.adjust(1)
    return builder.as_markup()

def inline_kb_next(top,j) :
   builder = InlineKeyboardBuilder()
   k=j+1
   builder.row(        
        InlineKeyboardButton(
            text='Следующий вопрос / Next task',           
            callback_data=f'next_{top}_{k}'
        )
    )
   builder.adjust(1)
   return builder.as_markup()

def inline_kb_explain(top,j,k) :
   builder = InlineKeyboardBuilder()
   builder.row(
        InlineKeyboardButton(
            text='Повторить / One more time!',
            callback_data='next_'+top+'_'+str(j)   #'explain'
        )
    )
   topiclink  = topic_links.get(k.get('subtopic',''),'') or topic_links.get(k.get('topic'),'')
   if topiclink :
      
      builder.row(
        InlineKeyboardButton(
            text='Обсудить задачу / Ask a question' ,
            url=topiclink
        )
      )
   builder.row(
        InlineKeyboardButton(
            text='Следующий вопрос / Next question',
            callback_data='next_'+top+'_'+str(j+1)
        )
    )
   builder.adjust(1)
   return builder.as_markup()
    

@router.callback_query(F.data.startswith('qst_'))
async def cmd_start(call: CallbackQuery):
    correct_h={'A' : '0' , 'B' : '1' , 'C' : '2', 'D':'3', 'E':'4'}
    await call.answer()
    ans = call.data.replace('qst_', '').split('_')
    top = ans[0]
    if len(top) < 2 :
       j = 0
       
    else :
       j = int(ans[1])
    ans_id = ans[2]
    k = kapibara[top][j]
    correct = correct_h[k["answer"]]
    ansok=0
    if correct == ans_id :
            msg_text = "Верно!  / Great!"
            reply=inline_kb_next(top,j)
            #   5044134455711629726
            #   5046509860389126442
            message_effect_id="5044134455711629726"
            ansok=1
    else :
            msg_text = "Нет, это не так( / Sorry, you are wrong"+"\n\n"
          #  if j > 2 and j < 6  : 
          #    msg_text += "Не знаешь как решить?  https://t.me/milgecru/385"
            reply=inline_kb_explain(top,j,k)
            message_effect_id=""
            ansok =0 
    log(call.from_user,[top,j,ans_id,ansok])
    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        await call.message.answer(msg_text, reply_markup=reply, message_effect_id=message_effect_id)

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    log(message.from_user,['start', message.text])
    await message.answer("Hi!! I am a CSCA Math Bot", reply_markup=start_kb())
    usr = message.from_user
    dt = str(datetime.datetime.now())
    id = str(usr.id)  
    username = usr.username
    l = [dt, id, username]  
    with open("/data/usr.txt", "a") as f:
                   f.write("\t".join(l+[message.text])+"\n")
    if 'stepik' in message.text :
      stepik.add(str(usr.id))
   
   # await message.answer("Это тестовая версия бота. Нашел ошибку? Есть идея? Пиши @csca_math_exam или прямо здесь.", reply_markup=start_kb()) 
   # await message.answer("Видео-разборы задач в группе https://t.me/milgecru/385") 
    
   
    
    #j=0
    #k = kapibara[j]
   # await  message.answer(k["english"]+"\n" + k["chinese"],  reply_markup=inline_kb(j))


@router.callback_query(F.data.startswith('explain'))
async def cmd_start(call: CallbackQuery):
    
    ans = call.data.replace('explain_', '').split('_')
    top =ans[0]
    j = int(ans[1])
    log(call.from_user,['explain', top, j])
    k = kapibara[top][j]

    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text='Получить доступ в канал',
            url='https://t.me/+c1ksuGkuO1BiNDk6'
        )
    )
    builder.adjust(1)
   
    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        await call.message.answer(k.get('link', "ooops, no link here"),reply_markup=builder.as_markup())
        
       


@router.callback_query(F.data.startswith('next'))
async def cmd_start(call: CallbackQuery):
    
    showvideo = 1
    user = call.from_user.username
    if user in ['evangecalista'] or str(call.from_user.id) in stepik :
      showvideo = 0
    
    
    ans = call.data.replace('next_', '').split('_')
    top =ans[0]
    j = int(ans[1])
    log(call.from_user,['next', top, j])
    
    #log(message.from_user,['log', str(ans) , top, str(j), str(kapibara[top])s ])
    if j == 0 :
          user_status = await bot.get_chat_member(chat_id="@csca_math_exam", user_id=call.message.chat.id)
          log(call.from_user,['status', str(user_status) ])
          
    if j == 7 :
          message=makeinvite(call.from_user)  
          async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"): 
             log(call.from_user,['invite'])
             await  call.message.answer(message)

          
          
    if j>= len(kapibara[top]) :
            j-=1
            async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
                  log(call.from_user,['end'])
                  await  call.message.answer('Больше нет вопросов по этой теме. Скоро добавлю новые вопросы.',reply_markup=start_kb())
                  
    else :
     async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        k = kapibara[top][j]
        if 'img' in k :
             photo_path = "/data/images/"+k['img']
             await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))

            # await bot.send_photo(chat_id=call.message.chat.id, photo=photo)
           
        await  call.message.answer(k["english"]+"\n" + k.get("chinese",'') +k.get("long",''),  reply_markup=inline_kb(top,j,showvideo))
    

@router.message()
async def cmd_start(message: Message):
    j = 0
    k = kapibara[j]
    
    log(message.from_user,['message', message.text.replace("\n"," ") ])
    async with ChatActionSender(bot=bot, chat_id=message.chat.id, action="typing"):
      await message.answer( "Спасибо! Передам сообщение разработчикам")
      await message.answer( "Чтобы продолжить, ответь на любой предыдущий вопрос")
      await message.answer( "Или начни сначала")
      await message.answer( k["english"]+"\n" + k.get("chinese",""), reply_markup=start_kb())



# Запуск процесса поллинга новых апдейтов
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())