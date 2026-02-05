#!/usr/bin/env python
import asyncio
import logging
import datetime
import json
import os
from aiogram import Bot, Dispatcher, types
from aiogram.filters.command import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import Router, F
from aiogram.types import CallbackQuery
from aiogram.types import Message
from aiogram.utils.chat_action import ChatActionSender
import db


API_TOKEN = os.environ.get('BOT_TOKEN', '8211322326:AAFbYxJ-qI0ERUJOUygYSbOzAfXK-vjt0us')

# Базовые пути
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("BOT_DATA_DIR", os.path.join(BASE_DIR, "data"))

# Создаём директорию data, если её нет
os.makedirs(DATA_DIR, exist_ok=True)


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

# Список пользователей с особым поведением (будет загружен из БД при старте)
stepik = set()


usrh={}
usrok={}
usrno={}
topics = []

kapibara = {}

# Загружаем вопросы из файлов
for fname in ['data', 'data_jan'] :
    try:
        with open(os.path.join(DATA_DIR, fname+'.txt'), 'r', encoding='utf-8') as f:  
            kpb = json.load(f) 
        
        for k in kpb :
            if 'topic' in k and k['topic'] not in topics : 
                topics = topics+[k['topic']] 
        
        for top in topics : 
            kapibara[top] = kapibara.get(top,[])+[x for x in kpb if x.get('topic','') == top] 
        kpb = {}
    except FileNotFoundError:
        logging.warning(f"Файл {fname}.txt не найден, пропускаем")
    except Exception as e:
        logging.error(f"Ошибка загрузки {fname}.txt: {e}")

try:
    with open(os.path.join(DATA_DIR, 'data_physics.txt'), 'r', encoding='utf-8') as f:  
        kpb = json.load(f) 
    
    if 'physics' not in topics:
        topics = topics+['physics'] 
    for top in ['physics'] : 
        kapibara[top] = [x for x in kpb if x.get('topic','') == top]
except FileNotFoundError:
    logging.warning("Файл data_physics.txt не найден, пропускаем")
except Exception as e:
    logging.error(f"Ошибка загрузки data_physics.txt: {e}") 

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

# Соединение с БД (будет инициализировано при старте)
db_conn = None

def log(usr, lg = []) :
   dt = datetime.datetime.now()
   id = usr.id  
   username = usr.username
   l = [dt, id, username] +  lg
   # Сохраняем в файл для обратной совместимости
   res_file = os.path.join(DATA_DIR, "res.txt")
   try:
       with open(res_file, "a", encoding='utf-8') as f:
           f.write("\t".join([str(x) for x in l])+"\n")
   except:
       pass  # Если файл недоступен, продолжаем работу

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

async def start_kb(user_id: int = None) -> InlineKeyboardMarkup:
    """
    Создаёт клавиатуру главного меню.
    Если передан user_id, добавляет кнопку "Продолжить" для тем с незавершённым прогрессом.
    """
    builder = InlineKeyboardBuilder()
    
    # Добавляем кнопку "Продолжить", если есть прогресс
    if user_id and db_conn:
        try:
            progress_list = await db.get_progress(db_conn, user_id)
            for prog in progress_list:
                topic = prog['topic']
                last_idx = prog['last_question_index']
                # Проверяем, что есть ещё вопросы
                if topic in kapibara and last_idx < len(kapibara[topic]):
                    builder.add(
                        InlineKeyboardButton(
                            text=f"▶️ Продолжить {topic[0].upper()+topic[1:]} ({last_idx}/{len(kapibara[topic])})",
                            callback_data=f'next_{topic}_{last_idx}'
                        )
                    )
        except Exception as e:
            logging.error(f"Ошибка получения прогресса для start_kb: {e}")
            pass  # Если ошибка при получении прогресса, просто показываем обычное меню
    
    # Добавляем все темы
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
    
    # Сохраняем ответ в БД
    if db_conn:
        try:
            await db.save_answer(
                db_conn,
                call.from_user.id,
                top,
                j,
                int(ans_id),
                ansok == 1
            )
        except Exception as e:
            logging.error(f"Ошибка сохранения ответа в БД: {e}")
    
    log(call.from_user,[top,j,ans_id,ansok])
    async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        await call.message.answer(msg_text, reply_markup=reply, message_effect_id=message_effect_id)

@router.message(Command("start"))
async def cmd_start(message: types.Message):
    log(message.from_user,['start', message.text])
    
    # Извлекаем аргумент команды (текст после /start)
    start_text = None
    if message.text and len(message.text.split()) > 1:
        start_text = ' '.join(message.text.split()[1:])
    
    # Сохраняем/обновляем пользователя в БД
    if db_conn:
        try:
            await db.ensure_user(db_conn, message.from_user, start_text)
        except Exception as e:
            logging.error(f"Ошибка сохранения пользователя в БД: {e}")
    
    # Сохраняем в файл для обратной совместимости
    usr = message.from_user
    dt = str(datetime.datetime.now())
    id = str(usr.id)  
    username = usr.username
    l = [dt, id, username]  
    usr_file = os.path.join(DATA_DIR, "usr.txt")
    try:
        with open(usr_file, "a", encoding='utf-8') as f:
            f.write("\t".join(l+[message.text or ''])+"\n")
    except:
        pass
    
    # Обновляем список stepik из БД
    if db_conn and 'stepik' in (start_text or '').lower():
        try:
            stepik_ids = await db.get_users_by_source(db_conn, 'stepik')
            stepik.update(str(uid) for uid in stepik_ids)
        except:
            pass
    
    kb = await start_kb(message.from_user.id)
    await message.answer("Hi!! I am a CSCA Math Bot", reply_markup=kb)
   
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
    
    # Проверяем, нужно ли скрывать видео для этого пользователя
    if user in ['evangecalista']:
        showvideo = 0
    elif db_conn:
        try:
            # Проверяем source пользователя в БД
            cursor = await db_conn.execute(
                "SELECT source FROM users WHERE id = ?",
                (call.from_user.id,)
            )
            row = await cursor.fetchone()
            if row and row[0] == 'stepik':
                showvideo = 0
        except:
            pass
    elif str(call.from_user.id) in stepik:
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
                  kb = await start_kb(call.from_user.id)
                  await  call.message.answer('Больше нет вопросов по этой теме. Скоро добавлю новые вопросы.',reply_markup=kb)
                  
    else :
     async with ChatActionSender(bot=bot, chat_id=call.from_user.id, action="typing"):
        k = kapibara[top][j]
        if 'img' in k :
             photo_path = os.path.join(DATA_DIR, "images", k['img'])
             await bot.send_photo(call.message.chat.id, photo=types.FSInputFile(photo_path))

            # await bot.send_photo(chat_id=call.message.chat.id, photo=photo)
           
        # Формируем текст вопроса с номером
        total_questions = len(kapibara[top])
        question_num = f"Вопрос {j+1} из {total_questions} / Question {j+1} of {total_questions}\n\n"
        question_text = question_num + k["english"]+"\n" + k.get("chinese",'') +k.get("long",'')
        await  call.message.answer(question_text,  reply_markup=inline_kb(top,j,showvideo))
    

@router.message(Command("stats"))
async def cmd_stats(message: types.Message):
    """Команда для просмотра статистики пользователя"""
    if not db_conn:
        await message.answer("Статистика временно недоступна.")
        return
    
    try:
        stats = await db.get_user_stats(db_conn, message.from_user.id)
        
        if stats['total_answered'] == 0:
            await message.answer("Вы ещё не ответили ни на один вопрос. Начните с команды /start!")
            return
        
        # Формируем сообщение со статистикой
        lang = message.from_user.language_code or 'en'
        if lang == 'ru':
            msg = f"📊 Ваша статистика:\n\n"
            msg += f"Всего ответов: {stats['total_answered']}\n"
            msg += f"Правильных: {stats['total_correct']}\n"
            accuracy = (stats['total_correct'] / stats['total_answered'] * 100) if stats['total_answered'] > 0 else 0
            msg += f"Точность: {accuracy:.1f}%\n\n"
            msg += "По темам:\n"
            for topic_stat in stats['by_topic']:
                msg += f"• {topic_stat['topic']}: {topic_stat['answered']} ответов, {topic_stat['correct']} правильных ({topic_stat['accuracy']:.1f}%)\n"
        else:
            msg = f"📊 Your statistics:\n\n"
            msg += f"Total answers: {stats['total_answered']}\n"
            msg += f"Correct: {stats['total_correct']}\n"
            accuracy = (stats['total_correct'] / stats['total_answered'] * 100) if stats['total_answered'] > 0 else 0
            msg += f"Accuracy: {accuracy:.1f}%\n\n"
            msg += "By topics:\n"
            for topic_stat in stats['by_topic']:
                msg += f"• {topic_stat['topic']}: {topic_stat['answered']} answers, {topic_stat['correct']} correct ({topic_stat['accuracy']:.1f}%)\n"
        
        kb = await start_kb(message.from_user.id)
        await message.answer(msg, reply_markup=kb)
    except Exception as e:
        logging.error(f"Ошибка получения статистики: {e}")
        await message.answer("Ошибка при получении статистики. Попробуйте позже.")


@router.message()
async def cmd_start(message: Message):
    # Исправление бага: kapibara - это словарь, нужно брать первую тему
    if topics and topics[0] in kapibara and len(kapibara[topics[0]]) > 0:
        k = kapibara[topics[0]][0]
        question_text = k["english"]+"\n" + k.get("chinese","")
    else:
        question_text = "Выберите тему для начала."
    
    log(message.from_user,['message', message.text.replace("\n"," ") if message.text else "" ])
    async with ChatActionSender(bot=bot, chat_id=message.chat.id, action="typing"):
      await message.answer( "Спасибо! Передам сообщение разработчикам")
      await message.answer( "Чтобы продолжить, ответь на любой предыдущий вопрос")
      await message.answer( "Или начни сначала")
      kb = await start_kb(message.from_user.id)
      await message.answer( question_text, reply_markup=kb)



# Запуск процесса поллинга новых апдейтов
async def main():
    global db_conn, stepik
    
    # Инициализируем БД
    try:
        db_conn = await db.init_db()
        logging.info("База данных инициализирована")
        
        # Загружаем список пользователей с source='stepik' из БД
        stepik_ids = await db.get_users_by_source(db_conn, 'stepik')
        stepik.update(str(uid) for uid in stepik_ids)
        logging.info(f"Загружено {len(stepik)} пользователей с source='stepik'")
    except Exception as e:
        logging.error(f"Ошибка инициализации БД: {e}")
        db_conn = None
    
    try:
        await dp.start_polling(bot)
    finally:
        # Закрываем соединение с БД при остановке
        if db_conn:
            await db_conn.close()
            logging.info("Соединение с БД закрыто")

if __name__ == "__main__":
    # Настраиваем логирование
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    asyncio.run(main())