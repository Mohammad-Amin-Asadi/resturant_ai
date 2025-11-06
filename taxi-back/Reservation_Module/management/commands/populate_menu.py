from django.core.management.base import BaseCommand
from Reservation_Module.models import MenuItem


class Command(BaseCommand):
    help = 'Populate restaurant menu items'

    def handle(self, *args, **options):
        menu_data = [
            # غذای ایرانی
            {'name': 'ته چین مرغ', 'category': 'غذای ایرانی', 'original_price': 598000, 'final_price': 542700, 'discount_percent': 10, 'is_special': True},
            {'name': 'شیشلیک شاندیز', 'category': 'غذای ایرانی', 'original_price': 1180000, 'final_price': 1067000, 'discount_percent': 10, 'is_special': True},
            {'name': 'کباب شالتو مصری', 'category': 'غذای ایرانی', 'original_price': 534000, 'final_price': 492800, 'discount_percent': 10},
            {'name': 'کباب سلطانی', 'category': 'غذای ایرانی', 'original_price': 952000, 'final_price': 865900, 'discount_percent': 10},
            {'name': 'کاسه کباب اردبیلی', 'category': 'غذای ایرانی', 'original_price': 863000, 'final_price': 783200, 'discount_percent': 10},
            {'name': 'کباب ترش گیلانی', 'category': 'غذای ایرانی', 'original_price': 859000, 'final_price': 779700, 'discount_percent': 10},
            {'name': 'کباب چنجه اعیونی', 'category': 'غذای ایرانی', 'original_price': 853000, 'final_price': 773300, 'discount_percent': 10},
            {'name': 'کباب برگ ممتاز', 'category': 'غذای ایرانی', 'original_price': 857000, 'final_price': 777700, 'discount_percent': 10},
            {'name': 'کباب بناب مخصوص', 'category': 'غذای ایرانی', 'original_price': 497000, 'final_price': 453400, 'discount_percent': 10},
            {'name': 'آدنا کباب ترکی', 'category': 'غذای ایرانی', 'original_price': 398000, 'final_price': 358700, 'discount_percent': 10},
            {'name': 'کباب تبریزی', 'category': 'غذای ایرانی', 'original_price': 392000, 'final_price': 358300, 'discount_percent': 10},
            {'name': 'جوجه ترش گیلانی', 'category': 'غذای ایرانی', 'original_price': 321000, 'final_price': 293700, 'discount_percent': 10},
            {'name': 'کباب نگینی مخصوص', 'category': 'غذای ایرانی', 'original_price': 394000, 'final_price': 358400, 'discount_percent': 10},
            {'name': 'کباب وزیری ( میکس مخصوص )', 'category': 'غذای ایرانی', 'original_price': 364000, 'final_price': 331300, 'discount_percent': 10},
            {'name': 'جوجه با استخوان', 'category': 'غذای ایرانی', 'original_price': 323000, 'final_price': 295200, 'discount_percent': 10},
            {'name': 'کباب لقمه زعفرانی', 'category': 'غذای ایرانی', 'original_price': 381000, 'final_price': 347700, 'discount_percent': 10},
            {'name': 'جوجه کباب ممتاز', 'category': 'غذای ایرانی', 'original_price': 321000, 'final_price': 293800, 'discount_percent': 10},
            {'name': 'کتف و بال', 'category': 'غذای ایرانی', 'original_price': 221000, 'final_price': 201900, 'discount_percent': 10},
            {'name': 'کباب کوبیده', 'category': 'غذای ایرانی', 'original_price': 258000, 'final_price': 235500, 'discount_percent': 10},
            {'name': 'جوجه مخصوص', 'category': 'غذای ایرانی', 'original_price': 241000, 'final_price': 221100, 'discount_percent': 10},
            {'name': 'ماهیچه شاندیزی', 'category': 'غذای ایرانی', 'original_price': 891000, 'final_price': 808900, 'discount_percent': 10},
            {'name': 'گردن گوسفندی', 'category': 'غذای ایرانی', 'original_price': 889000, 'final_price': 807100, 'discount_percent': 10},
            {'name': 'ته چین بره', 'category': 'غذای ایرانی', 'original_price': 843000, 'final_price': 771200, 'discount_percent': 10},
            {'name': 'میگو کبابی', 'category': 'غذای ایرانی', 'original_price': 783000, 'final_price': 715000, 'discount_percent': 10},
            {'name': 'جوجه فسنجان ویژه', 'category': 'غذای ایرانی', 'original_price': 391000, 'final_price': 359500, 'discount_percent': 10},
            {'name': 'مرغ ترش', 'category': 'غذای ایرانی', 'original_price': 393000, 'final_price': 357700, 'discount_percent': 10},
            {'name': 'ماهی قزل آلا', 'category': 'غذای ایرانی', 'original_price': 481000, 'final_price': 441700, 'discount_percent': 10},
            {'name': 'اکبر جوجه گلوگاه', 'category': 'غذای ایرانی', 'original_price': 321000, 'final_price': 293100, 'discount_percent': 10},
            {'name': 'ران مرغ مخصوص', 'category': 'غذای ایرانی', 'original_price': 243000, 'final_price': 221700, 'discount_percent': 10},
            {'name': 'خورش قرمه سبزی', 'category': 'غذای ایرانی', 'original_price': 129000, 'final_price': 117700, 'discount_percent': 10},
            {'name': 'دیزی سنتی', 'category': 'غذای ایرانی', 'original_price': 351000, 'final_price': 318500, 'discount_percent': 10},
            {'name': 'چلو ساده', 'category': 'غذای ایرانی', 'original_price': 110000, 'final_price': 99200, 'discount_percent': 10},
            {'name': 'سبزی پلو', 'category': 'غذای ایرانی', 'original_price': 112000, 'final_price': 100800, 'discount_percent': 10},
            {'name': 'باقالی پلو', 'category': 'غذای ایرانی', 'original_price': 117000, 'final_price': 105500, 'discount_percent': 10},
            {'name': 'شوید پلو', 'category': 'غذای ایرانی', 'original_price': 113000, 'final_price': 101800, 'discount_percent': 10},
            {'name': 'زرشک پلو', 'category': 'غذای ایرانی', 'original_price': 113000, 'final_price': 101800, 'discount_percent': 10},
            {'name': 'سوپ روز', 'category': 'غذای ایرانی', 'original_price': 45000, 'final_price': 41000, 'discount_percent': 10},
            {'name': 'کشک بادمجان', 'category': 'غذای ایرانی', 'original_price': 75000, 'final_price': 67500, 'discount_percent': 10},
            {'name': 'کوبیده تک سیخ', 'category': 'غذای ایرانی', 'original_price': 125000, 'final_price': 114000, 'discount_percent': 10},
            {'name': 'گوجه', 'category': 'غذای ایرانی', 'original_price': 50000, 'final_price': 45000, 'discount_percent': 10},
            {'name': 'قیمه نثار', 'category': 'غذای ایرانی', 'original_price': 385000, 'final_price': 346500, 'discount_percent': 10},
            {'name': 'خورش قیمه', 'category': 'غذای ایرانی', 'original_price': 129000, 'final_price': 117700, 'discount_percent': 10},
            {'name': 'بیتی کباب', 'category': 'غذای ایرانی', 'original_price': 482000, 'final_price': 482000, 'discount_percent': 0},
            {'name': 'جوجه آشوکا هندی', 'category': 'غذای ایرانی', 'original_price': 293000, 'final_price': 263700, 'discount_percent': 10},
            {'name': 'میرزا قاسمی', 'category': 'غذای ایرانی', 'original_price': 50000, 'final_price': 45000, 'discount_percent': 10},
            {'name': 'قیمه بادمجان', 'category': 'غذای ایرانی', 'original_price': 105000, 'final_price': 94500, 'discount_percent': 10},
            
            # نوشیدنی
            {'name': 'نوشابه قوطی کوکا', 'category': 'نوشیدنی', 'original_price': 42000, 'final_price': 42000, 'discount_percent': 0},
            {'name': 'نوشابه قوطی فانتا', 'category': 'نوشیدنی', 'original_price': 42000, 'final_price': 42000, 'discount_percent': 0},
            {'name': 'نوشابه قوطی سپرایت', 'category': 'نوشیدنی', 'original_price': 42000, 'final_price': 42000, 'discount_percent': 0},
            {'name': 'نوشابه قوطی زیرو کوکا', 'category': 'نوشیدنی', 'original_price': 52000, 'final_price': 52000, 'discount_percent': 0},
            {'name': 'لیموناد شیشه', 'category': 'نوشیدنی', 'original_price': 31000, 'final_price': 31000, 'discount_percent': 0},
            {'name': 'دوغ ابعلی شیشه', 'category': 'نوشیدنی', 'original_price': 28000, 'final_price': 28000, 'discount_percent': 0},
            {'name': 'دوغ سنتی کوچک', 'category': 'نوشیدنی', 'original_price': 30000, 'final_price': 30000, 'discount_percent': 0},
            {'name': 'دلستر قوطی لیمو', 'category': 'نوشیدنی', 'original_price': 39000, 'final_price': 39000, 'discount_percent': 0},
            {'name': 'دلستر قوطی هلو', 'category': 'نوشیدنی', 'original_price': 39000, 'final_price': 39000, 'discount_percent': 0},
            {'name': 'دلستر قوطی استوایی', 'category': 'نوشیدنی', 'original_price': 39000, 'final_price': 39000, 'discount_percent': 0},
            {'name': 'نوشابه شیشه کوکا', 'category': 'نوشیدنی', 'original_price': 30000, 'final_price': 30000, 'discount_percent': 0},
            {'name': 'آب معدنی کوچک', 'category': 'نوشیدنی', 'original_price': 10000, 'final_price': 10000, 'discount_percent': 0},
            {'name': 'سانی نس', 'category': 'نوشیدنی', 'original_price': 42000, 'final_price': 42000, 'discount_percent': 0},
            {'name': 'انرژی زا', 'category': 'نوشیدنی', 'original_price': 45000, 'final_price': 45000, 'discount_percent': 0},
            {'name': 'نوشابه خانواده کوکا', 'category': 'نوشیدنی', 'original_price': 57000, 'final_price': 57000, 'discount_percent': 0},
            {'name': 'نوشابه خانواده فانتا', 'category': 'نوشیدنی', 'original_price': 57000, 'final_price': 57000, 'discount_percent': 0},
            {'name': 'نوشابه خانواده سپرایت', 'category': 'نوشیدنی', 'original_price': 57000, 'final_price': 57000, 'discount_percent': 0},
            {'name': 'لیموناد خانواده', 'category': 'نوشیدنی', 'original_price': 55000, 'final_price': 55000, 'discount_percent': 0},
            {'name': 'دوغ سنتی خانواده', 'category': 'نوشیدنی', 'original_price': 54000, 'final_price': 54000, 'discount_percent': 0},
            {'name': 'دلستر لیمو خانواده', 'category': 'نوشیدنی', 'original_price': 60000, 'final_price': 60000, 'discount_percent': 0},
            {'name': 'دلستر هلو خانواده', 'category': 'نوشیدنی', 'original_price': 60000, 'final_price': 60000, 'discount_percent': 0},
            {'name': 'دلستر استوایی خانواده', 'category': 'نوشیدنی', 'original_price': 60000, 'final_price': 60000, 'discount_percent': 0},
            {'name': 'نوشابه شیشه فانتا', 'category': 'نوشیدنی', 'original_price': 30000, 'final_price': 30000, 'discount_percent': 0},
            {'name': 'نوشابه شیشه سپرایت', 'category': 'نوشیدنی', 'original_price': 30000, 'final_price': 30000, 'discount_percent': 0},
            {'name': 'نوشابه بطری', 'category': 'نوشیدنی', 'original_price': 25000, 'final_price': 25000, 'discount_percent': 0},
            {'name': 'دلستر قوطی کلاسیک', 'category': 'نوشیدنی', 'original_price': 39000, 'final_price': 39000, 'discount_percent': 0},
            {'name': 'دوغ آبعلی خانواده', 'category': 'نوشیدنی', 'original_price': 40000, 'final_price': 40000, 'discount_percent': 0},
            {'name': 'دلستر انگور خانواده', 'category': 'نوشیدنی', 'original_price': 60000, 'final_price': 60000, 'discount_percent': 0},
            {'name': 'آب معدنی بزرگ', 'category': 'نوشیدنی', 'original_price': 16000, 'final_price': 16000, 'discount_percent': 0},
            
            # فست فود
            {'name': 'پیتزا مخصوص', 'category': 'فست فود', 'original_price': 365000, 'final_price': 365000, 'discount_percent': 0, 'is_special': True},
            {'name': 'پیتزا چیکن', 'category': 'فست فود', 'original_price': 395000, 'final_price': 395000, 'discount_percent': 0},
            {'name': 'پیتزا یونانی', 'category': 'فست فود', 'original_price': 385000, 'final_price': 385000, 'discount_percent': 0},
            {'name': 'پیتزا پپرونی', 'category': 'فست فود', 'original_price': 370000, 'final_price': 370000, 'discount_percent': 0},
            {'name': 'پیتزا رست بیف', 'category': 'فست فود', 'original_price': 520000, 'final_price': 520000, 'discount_percent': 0},
            {'name': 'پیتزا سبزیجات', 'category': 'فست فود', 'original_price': 310000, 'final_price': 310000, 'discount_percent': 0},
            {'name': 'چیپس و پنیر', 'category': 'فست فود', 'original_price': 320000, 'final_price': 320000, 'discount_percent': 0},
            {'name': 'سیب زمینی سرخ شده', 'category': 'فست فود', 'original_price': 280000, 'final_price': 280000, 'discount_percent': 0},
            {'name': 'سیب زمینی با قارچ و پنیر', 'category': 'فست فود', 'original_price': 370000, 'final_price': 370000, 'discount_percent': 0},
            {'name': 'سیب زمینی ویژه', 'category': 'فست فود', 'original_price': 380000, 'final_price': 380000, 'discount_percent': 0},
            {'name': 'ناگت مرغ', 'category': 'فست فود', 'original_price': 320000, 'final_price': 320000, 'discount_percent': 0},
            {'name': 'سینی دو نفره فست فود', 'category': 'فست فود', 'original_price': 630000, 'final_price': 630000, 'discount_percent': 0},
            {'name': 'سینی سه نفره فست فود', 'category': 'فست فود', 'original_price': 790000, 'final_price': 790000, 'discount_percent': 0},
            {'name': 'یونانی(باقلوایی)', 'category': 'فست فود', 'original_price': 335000, 'final_price': 335000, 'discount_percent': 0},
            {'name': 'قارچ سوخاری', 'category': 'فست فود', 'original_price': 230000, 'final_price': 230000, 'discount_percent': 0},
            {'name': 'پاستا چیکن آلفردو', 'category': 'فست فود', 'original_price': 471000, 'final_price': 471000, 'discount_percent': 0},
            {'name': 'همبرگر', 'category': 'فست فود', 'original_price': 270000, 'final_price': 270000, 'discount_percent': 0},
            {'name': 'چیز برگر', 'category': 'فست فود', 'original_price': 290000, 'final_price': 290000, 'discount_percent': 0},
            {'name': 'قارچ برگر', 'category': 'فست فود', 'original_price': 290000, 'final_price': 290000, 'discount_percent': 0},
            {'name': 'دوبل برگر قارچ', 'category': 'فست فود', 'original_price': 420000, 'final_price': 420000, 'discount_percent': 0},
            {'name': 'چیکن قارچ', 'category': 'فست فود', 'original_price': 290000, 'final_price': 290000, 'discount_percent': 0},
            {'name': 'چیکن چیز ماشروم', 'category': 'فست فود', 'original_price': 380000, 'final_price': 380000, 'discount_percent': 0},
            {'name': 'ژامبون تنوری', 'category': 'فست فود', 'original_price': 280000, 'final_price': 280000, 'discount_percent': 0},
            {'name': 'هات داگ پنیری', 'category': 'فست فود', 'original_price': 220000, 'final_price': 220000, 'discount_percent': 0},
            
            # سینی ها
            {'name': 'سینی درباری', 'category': 'سینی ها', 'original_price': 2100000, 'final_price': 2020000, 'discount_percent': 5},
            {'name': 'سینی شاهانه بزرگمهر', 'category': 'سینی ها', 'original_price': 3300000, 'final_price': 3155000, 'discount_percent': 5, 'is_special': True},
            {'name': 'سینی سه نفره', 'category': 'سینی ها', 'original_price': 998000, 'final_price': 948100, 'discount_percent': 5},
            {'name': 'سینی دو نفره', 'category': 'سینی ها', 'original_price': 748000, 'final_price': 710600, 'discount_percent': 5},
            
            # صبحانه
            {'name': 'نیمرو', 'category': 'صبحانه', 'original_price': 50000, 'final_price': 50000, 'discount_percent': 0},
            {'name': 'املت سبزیجات', 'category': 'صبحانه', 'original_price': 70000, 'final_price': 70000, 'discount_percent': 0},
            {'name': 'تخم مرغ آب پز', 'category': 'صبحانه', 'original_price': 40000, 'final_price': 40000, 'discount_percent': 0},
            {'name': 'هات داگ', 'category': 'صبحانه', 'original_price': 95000, 'final_price': 95000, 'discount_percent': 0},
            {'name': 'سوسیس تخم مرغ', 'category': 'صبحانه', 'original_price': 110000, 'final_price': 110000, 'discount_percent': 0},
            {'name': 'تخم مرغ اسفناج', 'category': 'صبحانه', 'original_price': 85000, 'final_price': 85000, 'discount_percent': 0},
            {'name': 'املت', 'category': 'صبحانه', 'original_price': 60000, 'final_price': 60000, 'discount_percent': 0},
            {'name': 'کره مربا', 'category': 'صبحانه', 'original_price': 50000, 'final_price': 50000, 'discount_percent': 0},
            {'name': 'پنیر و گردو گوجه خیار', 'category': 'صبحانه', 'original_price': 70000, 'final_price': 70000, 'discount_percent': 0},
            {'name': 'کره پنیر', 'category': 'صبحانه', 'original_price': 55000, 'final_price': 55000, 'discount_percent': 0},
            {'name': 'ارده شیره', 'category': 'صبحانه', 'original_price': 40000, 'final_price': 40000, 'discount_percent': 0},
            
            # پیش غذا
            {'name': 'سالاد تک نفره', 'category': 'پیش غذا', 'original_price': 70000, 'final_price': 70000, 'discount_percent': 0},
            {'name': 'سالاد سزار', 'category': 'پیش غذا', 'original_price': 215000, 'final_price': 215000, 'discount_percent': 0},
            {'name': 'سالاد اندونزی', 'category': 'پیش غذا', 'original_price': 40000, 'final_price': 40000, 'discount_percent': 0},
            {'name': 'سالاد ماکارونی', 'category': 'پیش غذا', 'original_price': 70000, 'final_price': 70000, 'discount_percent': 0},
            {'name': 'بورانی بادمجان', 'category': 'پیش غذا', 'original_price': 40000, 'final_price': 40000, 'discount_percent': 0},
            {'name': 'بورانی اسفناج', 'category': 'پیش غذا', 'original_price': 40000, 'final_price': 40000, 'discount_percent': 0},
            {'name': 'ماست خیار', 'category': 'پیش غذا', 'original_price': 40000, 'final_price': 40000, 'discount_percent': 0},
            {'name': 'ماست موسیر کاسه ای', 'category': 'پیش غذا', 'original_price': 50000, 'final_price': 50000, 'discount_percent': 0},
            {'name': 'ماست موسیر کوزه ای', 'category': 'پیش غذا', 'original_price': 50000, 'final_price': 50000, 'discount_percent': 0},
            {'name': 'ماست موسیر شرکتی', 'category': 'پیش غذا', 'original_price': 15000, 'final_price': 15000, 'discount_percent': 0},
            {'name': 'زیتون پرورده کاسه ای', 'category': 'پیش غذا', 'original_price': 70000, 'final_price': 70000, 'discount_percent': 0},
            {'name': 'زیتون شور کاسه ای', 'category': 'پیش غذا', 'original_price': 70000, 'final_price': 70000, 'discount_percent': 0},
            {'name': 'ترشی لیته', 'category': 'پیش غذا', 'original_price': 40000, 'final_price': 40000, 'discount_percent': 0},
            {'name': 'سالاد خانواده', 'category': 'پیش غذا', 'original_price': 250000, 'final_price': 250000, 'discount_percent': 0},
            {'name': 'سس تک نفره', 'category': 'پیش غذا', 'original_price': 2500, 'final_price': 2500, 'discount_percent': 0},
            {'name': 'سبزی ریحون', 'category': 'پیش غذا', 'original_price': 40000, 'final_price': 40000, 'discount_percent': 0},
            {'name': 'سالاد دو نفره', 'category': 'پیش غذا', 'original_price': 90000, 'final_price': 90000, 'discount_percent': 0},
            {'name': 'زیتون پرورده شرکتی', 'category': 'پیش غذا', 'original_price': 20000, 'final_price': 20000, 'discount_percent': 0},
            {'name': 'زیتون شور شرکتی', 'category': 'پیش غذا', 'original_price': 20000, 'final_price': 20000, 'discount_percent': 0},
            {'name': 'دوغ کوچک الیس', 'category': 'پیش غذا', 'original_price': 15000, 'final_price': 15000, 'discount_percent': 0},
        ]

        self.stdout.write('Clearing existing menu items...')
        MenuItem.objects.all().delete()

        self.stdout.write('Populating menu items...')
        created_count = 0
        for item_data in menu_data:
            MenuItem.objects.create(**item_data)
            created_count += 1

        self.stdout.write(self.style.SUCCESS(f'Successfully created {created_count} menu items'))
        
        # Show category breakdown
        categories = MenuItem.objects.values('category').distinct()
        for cat in categories:
            count = MenuItem.objects.filter(category=cat['category']).count()
            self.stdout.write(f"  {cat['category']}: {count} items")

