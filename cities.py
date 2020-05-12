import os
import sys

import requests
import random
import asyncio
import discord
from lxml import etree
from multiprocessing import Pool

TOKEN = "Njk5MjYwMzE1NzA3Mzc1NzE4.XpR1gw.FHCHYzlJfw_hLPA9R3pPLnOaVHI"


class ENetworkException(Exception):
    pass


def get_cities_by_letter(letter):
    site_request = "https://en.wikipedia.org/wiki/List_of_towns_and_cities_with_100,000_or_more_inhabitants/" + \
                   "cityname:_" + letter
    response = requests.get(site_request)
    if not response:
        raise ENetworkException(f"HTTP статус: {response.status_code} ({response.reason})")

    def recursive_text_search(item):
        result = []
        l_text = item.text
        l_text = "" if l_text is None else l_text.strip()
        if l_text != "":
            result += [l_text]
        for child in item:
            result += recursive_text_search(child)
        return result

    # Поиск таблицы и чтение ячеек
    cities_table = etree.HTML(response.text).find(".//table/tbody")
    table_cells = [recursive_text_search(row) for row in cities_table]

    # Вернём значение нулевого слолбца ("City") из строк, начиная с первой (кроме заголовка)
    return [pair[0] for pair in table_cells[1:]]


def get_translation_for_cities(cities, lang_source, lang_destination):
    text = "\n".join(cities)
    tran_request_params = {
        "key": "trnsl.1.1.20200511T151858Z.7d85623407090262.809bb0f260ad7b42d952d86165282cd125e2a00b",
        "text": text,
        "lang": lang_source + "-" + lang_destination
    }
    response = requests.get("https://translate.yandex.net/api/v1.5/tr.json/translate", params=tran_request_params)
    if not response:
        raise ENetworkException(f"HTTP статус: {response.status_code} ({response.reason})")

    return response.json()["text"][0].split("\n")


class BotClient(discord.Client):
    help_message = "/start - начать игру\n/lang (en/ru) - выбрать язык\n/help - помощь"

    def __init__(self):
        super().__init__()
        self.x, self.y, self.s = None, None, None
        self.cities_orig = dict()
        self.cities_by_letters = dict()
        self.letters_to_ignore_orig = dict()
        self.letters_to_ignore = None
        self.city = None
        self.lost = None
        random.seed()

    def __del__(self):
        if hasattr(self, "map_file"):
            os.remove(self.map_file)

    def load_cities(self):
        try:
            self.cities_orig["en"] = self.get_cities_list()
            self.cities_orig["ru"] = self.get_translation_for_cities("ru")
            self.letters_to_ignore_orig["en"] = []
            self.letters_to_ignore_orig["ru"] = ["Ь", "Ъ", "Ы"]
        except ENetworkException as E:
            QErrorMessage().showMessage(E)
            sys.exit(-1)

    def init_game(self, lang):
        self.cities_by_letters.clear()
        for cities_by_letter in self.cities_orig[lang]:
            self.cities_by_letters[cities_by_letter[0][0]] = cities_by_letter
        self.letters_to_ignore = self.letters_to_ignore_orig[lang]
        self.city = None
        self.lost = False

    async def on_message(self, message):
        if message.author == self.user:
            return

        def delete_city_from_availables(city):
            letter = city[0].upper()
            for city_iter in self.cities_by_letters[letter]:
                if city_iter.lower() == city.lower():
                    self.cities_by_letters[letter].remove(city_iter)
                    break
            if len(self.cities_by_letters[letter]) == 0:
                self.cities_by_letters.pop(letter)

        def get_last_readable_letter(city):
            if len(self.letters_to_ignore) == 0:
                return city[-1]
            for letter in city[::-1]:
                if not letter.upper() in self.letters_to_ignore:
                    return letter

        async def start_game():
            if random.random() < 0.5:
                letter = random.choice(tuple(self.cities_by_letters.keys()))
                await choose_city_and_send(letter)
            else:
                await message.channel.send("Ваш ход")

        async def choose_city_and_send(letter):
            self.city = random.choice(self.cities_by_letters[letter])
            self.findLocation(self.city)
            self.getImage()
            delete_city_from_availables(self.city)
            await message.channel.send(self.city, file=discord.File(self.map_file))

        text = message.content.strip().lower()
        if text == "/start":
            self.init_game("ru")
            await start_game()
        elif text[0: 5] == "/lang":
            parts = text.split()
            if len(parts) < 2:
                await message.channel.send("Не понял")
            else:
                lang = parts[1].strip().lower()
                if not lang in ("en", "ru"):
                    await message.channel.send("Неизвестный язык")
                else:
                    self.init_game(lang)
                    await start_game()
        elif text == "/help":
            await message.channel.send(type(self).help_message)
        else:
            if self.lost:
                await message.channel.send("Не бей лежачего! Я же сдался!")
            elif len(self.cities_by_letters.keys()) == 0:
                await message.channel.send("Начни игру >:[")
            elif len(text) == 0 or not text[0].isalpha():
                await message.channel.send("Не понял")
            else:
                if self.city is not None and text[0].upper() != get_last_readable_letter(self.city).upper():
                    await message.channel.send("Не с той буквы")
                else:
                    # Проверим, есть ли такой город вообще
                    if not self.findLocation(text):
                        await message.channel.send("А вот нет такого города!")
                    else:
                        letter = get_last_readable_letter(text).upper()
                        if letter in self.cities_by_letters.keys():
                            if text[0].upper() in self.cities_by_letters.keys():
                                delete_city_from_availables(text)
                            await choose_city_and_send(letter)
                        else:
                            self.lost = True
                            await message.channel.send("Вы победили! Ух! Ах! Сдаюсь!")

    async def on_ready(self):
        for guild in self.guilds:
            for text_channel in guild.text_channels:
                await text_channel.send(type(self).help_message)

    @staticmethod
    def get_cities_list():
        pool = Pool()
        # Запросы к страницам Википедии (пробег по буквам от "A" до "Z")
        # result = pool.map(get_cities_by_letter, [chr(ord("A") + i) for i in range(0, 26)])  # Иногда зависает в конце
        result = [get_cities_by_letter(letter) for letter in [chr(ord("A") + i) for i in range(0, 26)]]
        pool.close()
        pool.join()
        return result

    def get_translation_for_cities(self, lang):
        pool = Pool()
        # Запросы к Яндекс.Переводу. Переводим с английского по частям: сначала города на "A", потом на "B"...
        # translated_cities_parts = pool.starmap(get_translation_for_cities,                  # Не тестировалось
        #                                        [(cities_by_letter, "en", lang) for cities_by_letter in
        #                                         self.cities_orig["en"]])
        translated_cities_parts = [get_translation_for_cities(cities_by_letter, "en", lang)
                                   for cities_by_letter in self.cities_orig["en"]]
        pool.close()
        pool.join()

        if lang == "ru":
            locale_letters = [chr(ord("А") + i) for i in range(0, 26)]
        else:
            locale_letters = []
        cities_dict_by_letters = {}
        for translated_cities_part in translated_cities_parts:
            for city in translated_cities_part:
                # Защита от некорректного перевода. Все вопросы - к англоязычной Википедии и Яндекс.Переводу
                city = city.strip().strip(",")
                if not city[0].isalpha():
                    continue
                if len(locale_letters) > 0:
                    not_translated = False
                    for letter in city:
                        if letter.isalpha() and not letter.upper() in locale_letters:
                            not_translated = True
                    if not_translated:
                        continue
                if not city[0] in cities_dict_by_letters:
                    cities_dict_by_letters[city[0]] = []
                cities_dict_by_letters[city[0]].append(city)
        return [sorted(cities_dict_by_letters[letter]) for letter in sorted(cities_dict_by_letters.keys())]

    def findLocation(self, location):
        loc_request_params = {
            "geocode": location,
            "apikey": "40d1649f-0493-4b70-98ba-98533de7710b",
            "kind": "locality",
            "format": "json",
            "results": "100"
        }
        response = requests.get("https://geocode-maps.yandex.ru/1.x", params=loc_request_params)
        if not response:
            raise ENetworkException(f"HTTP статус: {response.status_code} ({response.reason})")

        features = response.json()["response"]["GeoObjectCollection"]["featureMember"]
        if len(features) == 0:
            return False

        geo_obj = features[0]["GeoObject"]
        x_s, y_s = geo_obj["Point"]["pos"].split(" ")  # Позиция
        envelope = geo_obj["boundedBy"]["Envelope"]  # Границы
        p1, p2, p3, p4 = envelope["lowerCorner"].split(" ") + envelope["upperCorner"].split(" ")
        p1, p2, p3, p4 = float(p1), float(p2), float(p3), float(p4)

        self.x, self.y, self.s = float(x_s), float(y_s), round((p3 - p1 + p4 - p2) / 2, 9)
        self.getImage()
        return True

    def getImage(self):
        # Положение
        if self.x is None or self.y is None or self.s is None:
            return
        p1, p2, p3, p4 = self.x - self.s, self.y - self.s, self.x + self.s, self.y + self.s
        p1, p2, p3, p4 = min(max(p1, -175), 175), min(max(p2, -85), 85), min(max(p3, -175), 175), min(max(p4, -85), 85)
        map_request = f"""http://static-maps.yandex.ru/1.x/?bbox={str(p1)},{str(p2)}~{str(p3)},{str(p4)}&l=sat,skl"""
        # Режим
        response = requests.get(map_request)
        if not response:
            raise ENetworkException(f"HTTP статус: {response.status_code} ({response.reason})")

        self.map_file = "map.png"
        with open(self.map_file, "wb") as file:
            file.write(response.content)


if __name__ == '__main__':
    bot = BotClient()
    bot.load_cities()
    bot.run(TOKEN)
