import os
import io
import re
import telebot
from google.cloud import vision_v1
import requests
from eskiz_sms import EskizSMS
import random
import redis
from decouple import config

# Set up the Google Cloud Vision API client
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = r"sustained-axis-396810-1dd4fea38af3.json"
client = vision_v1.ImageAnnotatorClient()

# Set up the Telebot instance
bot_token = config('bot_token')
bot = telebot.TeleBot(bot_token)

# Your Django API endpoint for checking user availability
API_URL = config('API_URL')

# Define the username and password of Django admin
username = config('username')
password = config('pwd')

# Initialize a Redis client
redis_client = redis.StrictRedis(host='localhost', port=6379)

# Define the JWT tokens
jwt_access_token = None

edited_text_dict = {}
edited_text_buttons = {}
user_messages = {}
worker_data = {}

prodaja_check = False
summa_check = False
doc_check = False

verification_codes = {}
list_of_checks = ['Сканировать чек', 'Сканировать документ']

def get_token(username, password):
    global jwt_access_token
    token_endpoint = f'{API_URL}token/'  # Replace with the actual token endpoint
    data = {
        'username': username,
        'password': password,
    }
    try:
        response = requests.post(token_endpoint, data=data)
        if response.status_code == 200:
            jwt_access_token = response.json().get('access')
            print("Token got successfully.")
        else:
            print(f"Token get failed with status code {response.status_code}")
    except Exception as e:
        print(f"Error getting token: {e}")

def update_user(user_id, worker_data, headers):
    data_update = {
                    'id_tg': user_id,
                }
    response_update = requests.patch(f'{API_URL}worker/{worker_data["id"]}', data=data_update, headers=headers)
    if response_update.status_code == 200:
        print('Successfully updated')
    else:
        print(f'Failed to update: {response_update.status_code}')

    print(f"Response text: {response_update.text}")
    print(f'Worker Data: {worker_data}')
    print(f'DATA: {data_update}, ID: {worker_data["id"]}')

# Handle the '/start' command
@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    bot.send_message(user_id, 'Пожалуйста, отправьте свой номер телефона:', reply_markup=create_phone_number_button())
    # Store the user's current step in Redis
    redis_client.set(f'user_step:{user_id}', 'zero')

    get_token(username, password)

def create_phone_number_button():
    markup = telebot.types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    phone_button = telebot.types.KeyboardButton(text="Отправить номер телефона", request_contact=True)
    markup.add(phone_button)
    return markup

def send_SMS(user_phone, random_code):
    email = config('email')
    password = config('password')
    cleaned_number = re.sub(r'\+', '', user_phone)
    eskiz = EskizSMS(email=email, password=password)
    eskiz.send_sms(cleaned_number, f'<#> Your verification code Avtoritet: {random_code}', from_whom='4546', callback_url=None)

#Handle the phone number
@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    global worker_data, verification_codes
    user_id = message.from_user.id
    user_phone = message.contact.phone_number
    contact = message.contact

    # Make sure the phone number is in the correct format
    if '+' != user_phone[0]:
        user_phone = f'+{user_phone}'

    # Check if the contact belongs to the same user
    if contact.user_id == user_id:
        # Make an API call to check if the user is allowed
        try:
            headers = {
                'Authorization': f'Bearer {jwt_access_token}',  # Include the JWT token in the Authorization header
            }
            response = requests.get(f'{API_URL}worker/{user_phone}', headers=headers)
            if response.status_code == 200:
                print("Access token is still usabel!")
            else:
                print("Access token is no longer usable! Trying to refresh it...")
                get_token(username, password)
        except Exception as e:
            print(f"Error getting token: {e}")

        headers = {'Authorization': f'Bearer {jwt_access_token}',}
        response = requests.get(f'{API_URL}worker/{user_phone}', headers=headers)
        if response.status_code == 200:
            worker_data = response.json()
            verification_code = ''.join(random.choice('0123456789') for i in range(6))
            verification_codes[user_id] = verification_code
            send_SMS(user_phone, verification_codes[user_id])
            update_user(user_id, headers, worker_data)

            # User is allowed, proceed with the bot's functionality
            markup = telebot.types.ReplyKeyboardRemove(selective=False)
            bot.send_message(user_id, 'Код подтверждения успешно отправлен, введите код:', reply_markup=markup)
            # Store the user's current step in Redis
            redis_client.set(f'user_step:{user_id}', 'verification')
        else:
            # User is not allowed, send a warning message
            print(response.status_code)
            bot.send_message(user_id, 'К сожалению, вам не разрешено использовать этого бота.')
    else:
        bot.send_message(user_id, "Вы отправили чужой контакт. Пожалуйста, пришлите свой контактный номер!")

# Handle the verification code
@bot.message_handler(func=lambda message: redis_client.get(f'user_step:{message.from_user.id}') == b'verification')
def verify_user(message):
    user_id = message.from_user.id
    verification_code = message.text
    if verification_code == verification_codes[user_id]:
        if user_id != worker_data['id_tg']:
                try:
                    headers = {
                        'Authorization': f'Bearer {jwt_access_token}',  # Include the JWT token in the Authorization header
                    }
                    response = requests.get(f'{API_URL}worker/{worker_data["id"]}', headers=headers)
                    if response.status_code == 200:
                        print("Access token is still usabel!")
                    else:
                        print("Access token is no longer usable! Trying to refresh it...")
                        get_token(username, password)
                except Exception as e:
                    print(f"Error getting token: {e}")

                headers = {
                        'Authorization': f'Bearer {jwt_access_token}',  # Include the JWT token in the Authorization header
                    }
                update_user(user_id, headers, worker_data)

        # Store the user's current step in Redis
        redis_client.set(f'user_step:{user_id}', 'choose_option')
        scan_options(message)  # Call the scan_options function to proceed
    else:
        bot.send_message(user_id, 'Код не верный, попробуйте снова.')

# Handle the Other remainings
@bot.message_handler(func=lambda message: redis_client.get(f'user_step:{message.from_user.id}') == b'choose_option')
def scan_options(message):
    user_id = message.from_user.id
    markup = telebot.types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    check_button = telebot.types.KeyboardButton(text='Сканировать чек')
    document_button = telebot.types.KeyboardButton(text='Сканировать документ')
    markup.add(check_button, document_button)
    bot.send_message(message.chat.id, 'Пожалуйста, выберите опцию:', reply_markup=markup)
    # Store the user's current step in Redis
    redis_client.set(f'user_step:{user_id}', 'selection')

# Handle the chosen option (Check or Document)
@bot.message_handler(func=lambda message: message.text in list_of_checks and redis_client.get(f'user_step:{message.from_user.id}'))
def handle_option(message):
    global user_language
    user_id = message.from_user.id
    listof = ['selection', 'send_check_photo', 'send_document_photo']
    # Retrieve the user's current step from Redis
    user_step_bytes = redis_client.get(f'user_step:{user_id}')
    user_step_str = user_step_bytes.decode('utf-8')
    if user_step_str in listof:
        # Check if there's a previous message to delete
        if user_id in user_messages:
            try:
                bot.delete_message(user_id, user_messages[user_id])
            except:
                pass

        if message.text == list_of_checks[0]:
            bot.send_message(user_id, 'Пожалуйста, отправьте новое фото чека.')
            # Store the user's current step in Redis
            redis_client.set(f'user_step:{user_id}', 'send_check_photo')

        elif message.text == list_of_checks[1]:
            bot.send_message(user_id, 'Пожалуйста, отправьте новое фото документа.')
            # Store the user's current step in Redis
            redis_client.set(f'user_step:{user_id}', 'send_document_photo')
    else:
        bot.send_message(user_id, "Пожалуйста, продолжайте с того места, на котором остановились!")
        
# Handle received photos
@bot.message_handler(content_types=['photo'])
def handle_photo(message):

    global summa_check, prodaja_check, doc_check, image_file, worker_data

    user_id = message.from_user.id

    try:
        headers = {
            'Authorization': f'Bearer {jwt_access_token}',  # Include the JWT token in the Authorization header
        }
        response = requests.get(f'{API_URL}worker/id/{user_id}', headers=headers)
        if response.status_code == 200:
            # worker_data = response.json()
            print("Access token is still usabel!")
        else:
            print("Access token is no longer usable! Trying to refresh it...")
            get_token(username, password)
    except Exception as e:
        print(f"Error getting token: {e}")

    headers = {
            'Authorization': f'Bearer {jwt_access_token}',  # Include the JWT token in the Authorization header
        }

    response = requests.get(f'{API_URL}worker/id/{user_id}', headers=headers)
    if response.status_code == 200:
            worker_data[f'{user_id}'] = response.json()
    else:
        print(f"User does not exist with this {user_id} ID: {response.status_code}")
        worker_data = None

    if worker_data[f'{user_id}'] and user_id == worker_data[f'{user_id}']['id_tg']:

        # Retrieve the user's current step from Redis
        user_step_bytes = redis_client.get(f'user_step:{user_id}')
        user_step_str = user_step_bytes.decode('utf-8')
        
        edited_text_dict[user_id] = {'step': user_step_str}
        if user_step_str == "send_check_photo":
            file_id = message.photo[-1].file_id
            file_info = bot.get_file(file_id)

            # Download and process the image
            downloaded_file = bot.download_file(file_info.file_path)
            image_file = downloaded_file
            # Create an in-memory stream to read the image content
            image_stream = io.BytesIO(downloaded_file)
            # Create an Image object with the image content
            image = vision_v1.Image(content=image_stream.read())

            response = client.text_detection(image=image)
            texts = response.text_annotations

            if texts:
                extracted_text = texts[0].description
                # Split the extracted text into lines
                lines = extracted_text.split('\n')
                # Keywords to search for
                keywords = ["ПРОДАЖА"]
                prodazha_text = None
                # Search for keywords in each line
                prodazha_text = [line for line in lines if any(keyword in line for keyword in keywords)]

                # Initialize max_x
                max_x = 0

                # Loop through all detected texts to find the maximum x-coordinate
                for text in texts:
                    for vertex in text.bounding_poly.vertices:
                        max_x = max(max_x, vertex.x)

                # Find bounding box of first occurrence of "Сумма" and replace x-values with max_x
                summa_vertices = None
                for text in texts:
                    if text.description == "Сумма":
                        summa_vertices = [(vertex.x, vertex.y) for vertex in text.bounding_poly.vertices]
                        summa_vertices = [(max_x, vertex[1]) for vertex in summa_vertices]
                        break

                summa_text = None
                if summa_vertices:
                    # Extract texts within the new bounding box
                    captured_numbers = set()  # using set to avoid duplicates

                    # Regular expression pattern to find digits in a string
                    pattern = re.compile(r'\d+')

                    for text in texts:
                        for vertex in text.bounding_poly.vertices:
                            x, y = vertex.x, vertex.y
                            if summa_vertices[0][1] <= y <= summa_vertices[2][1]:
                                # Extract all digits from the text
                                match = pattern.findall(text.description)
                                if match:
                                    # Convert to integer and add to the set
                                    for num_str in match:
                                        captured_numbers.add(int(num_str))

                    if captured_numbers:
                        summa_text = f"Сумма {max(list(captured_numbers))}"
                        summa_check = True
                    else:
                        summa_text = 'Сумма not found'
                        summa_check = False
                # Send both "ПРОДАЖА" and "Сумма" together to the user along with edit and submit buttons
                response_text = ""

                if prodazha_text is not None:
                    response_text += '\n'.join(prodazha_text) + "\n"
                    # Search for № sign if it's correct or no
                    match = re.search('№', response_text)
                    if not match:
                        response_text = re.sub(r'ИС|МО', '№0', response_text)
                        response_text = re.sub(r'О', '0', response_text)

                    prodaja_check = True
                if summa_text is not None:
                    response_text += summa_text + "\n"

                if response_text:
                    send_text_with_buttons(user_id, response_text.strip())
                else:
                    bot.send_message(user_id, 'Соответствующих строк не обнаружено.')

        elif user_step_str == "send_document_photo":
            file_id = message.photo[-1].file_id
            file_info = bot.get_file(file_id)

            downloaded_file = bot.download_file(file_info.file_path)
            
            image_file = downloaded_file
            # Create an in-memory stream to read the image content
            image_stream = io.BytesIO(downloaded_file)
            # Create an Image object with the image content
            image = vision_v1.Image(content=image_stream.read())
            response = client.text_detection(image=image)
            texts = response.text_annotations

            if texts:

                extracted_text = texts[0].description
                # Split the extracted text into lines
                lines = extracted_text.split('\n')
                # Keywords to search for
                keywords = ["Перемещение товаров в производство"]
                prodazha_text = None
                # Search for keywords in each line
                prodazha_text = [line for line in lines if any(keyword in line for keyword in keywords)]

                response_text = ""
                if prodazha_text is not None:
                    # Extract only the part of the text that follows the keyword
                    response_text += '\n'.join([line.split(keywords[0])[-1].split('от')[0].strip() for line in prodazha_text]) + "\n"

                if response_text:
                    doc_check = True
                    result_string = re.sub(r'\s', '', response_text)
                    send_text_with_buttons(user_id, result_string.strip())
                else:
                    bot.send_message(user_id, 'Соответствующих строк не обнаружено.')

            else:
                bot.send_message(user_id, 'Текст в документе не найден.')
        else:
            bot.send_message(user_id, 'Пожалуйста, выберите вариант перед дальнейшей обработкой.')
        # del user_steps[user_id]  # Remove the user's step after processing
    else:
        bot.send_message(user_id, 'Кажется, вы не зарегистрированы, чтобы проверить, есть ли у вас разрешение - нажмите на 👉 /start .')

# Send extracted text with edit and submit buttons
def send_text_with_buttons(chat_id, extracted_text):
    
    markup = telebot.types.InlineKeyboardMarkup()
    edit_button = telebot.types.InlineKeyboardButton(text="Ресканировать", callback_data="edit")
    if summa_check  and prodaja_check or doc_check:
        submit_button = telebot.types.InlineKeyboardButton(text='Подтвердить', callback_data="submit")
        markup.add(edit_button, submit_button)
    else:
        markup.add(edit_button)

    edited_text_buttons[chat_id] = extracted_text
    if extracted_text:
        sent_message = bot.send_message(chat_id, extracted_text, reply_markup=markup)
        # Store the message ID in the user_messages dictionary
        user_messages[chat_id] = sent_message.message_id
    else:
        bot.send_message(chat_id, "Текст в документе не найден.")

#Handle the errors
@bot.message_handler(func=lambda message: True)
def error(message):
    user_id = message.from_user.id
    user_step_bytes = redis_client.get(f'user_step:{user_id}')
    user_step_str = user_step_bytes.decode('utf-8')
    if user_step_str == 'zero':
        bot.send_message(user_id, 'Пожалуйста, отправьте контакт.')
    elif user_step_str == 'selection':
        bot.send_message(user_id, 'Пожалуйста, выберите опцию для дальнейшей обработки.')
    elif user_step_str in ['send_check_photo', 'send_document_photo']:
        bot.send_message(user_id, 'Пожалуйста, отправьте новое фото чека или документ.')

# Handle the rescan and submit buttons
@bot.callback_query_handler(func=lambda call: True)
def button_callback(call):
    global image_file, worker_data
    user_id = call.from_user.id
    chat_id = call.message.chat.id
        
    try:
        headers = {
            'Authorization': f'Bearer {jwt_access_token}',  # Include the JWT token in the Authorization header
        }
        response = requests.get(f'{API_URL}worker/id/{user_id}', headers=headers)
        if response.status_code == 200:
            # worker_data = response.json()
            print("Access token is still usabel!")
        else:
            print("Access token is no longer usable! Trying to refresh it...")
            get_token(username, password)
    except Exception as e:
        print(f"Error getting token: {e}")

    headers = {
            'Authorization': f'Bearer {jwt_access_token}',  # Include the JWT token in the Authorization header
        }

    response = requests.get(f'{API_URL}worker/id/{user_id}', headers=headers)
    if response.status_code == 200:
            worker_data[f'{user_id}'] = response.json()
    else:
        print(f"Error getting token: {response.status_code}")
    
    if call.data == "edit":
        redis_client.set(f'user_step:{user_id}', edited_text_dict[user_id]['step'])
        user_step_bytes = redis_client.get(f'user_step:{user_id}')
        user_step_str = user_step_bytes.decode('utf-8')
        if user_step_str == "send_check_photo":
            bot.send_message(user_id, "Пожалуйста, отправьте новое фото чека.")
            bot.delete_message(chat_id, call.message.message_id)  # Remove the edit/submit buttons message
        elif user_step_str == "send_document_photo":
            bot.send_message(user_id, "Пожалуйста, отправьте новое фото документа.")
            bot.delete_message(chat_id, call.message.message_id)  # Remove the edit/submit buttons message
        edited_text_dict.pop(user_id, None)

    elif call.data == "submit":
        chat_id = call.message.chat.id
        edited_text = edited_text_buttons.get(chat_id)
        user_step_bytes = redis_client.get(f'user_step:{user_id}')
        user_step_str = user_step_bytes.decode('utf-8')

        if user_step_str == 'send_check_photo':
            # Search for the patterns in the text
            check_num_1 = re.search(r'№\d+', edited_text)
            check_num = check_num_1.group()

            check_sum_1 = re.search(r'Сумма (\d+)', edited_text)
            check_sum = check_sum_1.group(1)

            files = {'image': (f'{check_num}.jpg', image_file)}
            data = {
                'check_num': check_num,
                'sum': check_sum,
                'worker': int(worker_data[f'{user_id}']['id']),
                'branch': int(worker_data[f'{user_id}']['branch']),
            }
            # Saving the data with API
            try:
                headers = {
                    'Authorization': f'Bearer {jwt_access_token}',  # Include the JWT token in the Authorization header
                }
                response = requests.get(f'{API_URL}check/', headers=headers)
                if response.status_code == 200:
                    print("Access token is still usabel!")
                else:
                    print("Access token is no longer usable! Trying to refresh it...")
                    get_token(username, password)
            except Exception as e:
                print(f"Error getting token: {e}")

            headers = {
                    'Authorization': f'Bearer {jwt_access_token}',  # Include the JWT token in the Authorization header
                }

            response = requests.post(f'{API_URL}check/', data=data, files=files, headers=headers)
            if response.status_code == 201:
                bot.send_message(chat_id, "Чек успешно отправлен и сохранен.")
            elif response.status_code == 400:
                bot.send_message(chat_id, "Этот чек уже отправлен.")
            else:
                bot.send_message(chat_id, "Не удалось отправить текст на сервер. Пожалуйста, повторите попытку позже.")

        elif user_step_str == 'send_document_photo':
            files = {'image': (f'{edited_text}.jpg', image_file)}
            data = {
                'doc_num': edited_text,
                'worker': int(worker_data[f'{user_id}']['id']),
                'branch': int(worker_data[f'{user_id}']['branch']),
            }

            try:
                headers = {
                    'Authorization': f'Bearer {jwt_access_token}',  # Include the JWT token in the Authorization header
                }
                response = requests.get(f'{API_URL}doc/', headers=headers)
                if response.status_code == 200:
                    print("Access token is still usabel!")
                else:
                    print("Access token is no longer usable! Trying to refresh it...")
                    get_token(username, password)
            except Exception as e:
                print(f"Error getting token: {e}")

            headers = {
                    'Authorization': f'Bearer {jwt_access_token}',  # Include the JWT token in the Authorization header
                }

            # Saving the data with API
            response = requests.post(f'{API_URL}doc/', data=data, files=files, headers=headers)
            if response.status_code == 201:
                bot.send_message(chat_id, "Текст успешно отправлен и сохранен.")
            elif response.status_code == 400:
                bot.send_message(chat_id, "Этот документ уже отправлен.")
            else:
                bot.send_message(chat_id, "Не удалось отправить текст на сервер. Пожалуйста, повторите попытку позже.")
        
        bot.delete_message(chat_id, call.message.message_id)  # Remove the edit/submit buttons message

def main():
    bot.polling()

if __name__ == '__main__':
    main()
