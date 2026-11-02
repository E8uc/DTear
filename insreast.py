# مبرمج • Qrakr
# BY : ☀ TLE @Qrakr
import requests
import os
import getpass
import os
import requests
import sys
import time
import datetime
import re
import random
from threading import Thread
from rich import print as g
from rich.panel import Panel
E = '\033[1;31m'
Y = '\033[1;33m'
Z = '\033[1;31m' #احمر
X = '\033[1;33m' #اصفر
Z1 = '\033[2;31m' #احمر ثاني
F = '\033[2;32m' #اخضر
A = '\033[2;34m'#ازرق
C = '\033[2;35m' #وردي
S = '\033[2;36m'#سمائي
G = '\033[1;34m' #ازرق فاتح
HH='\033[1;34m' #ازرق فاتح
M = '\x1b[1;37m'#ابیض
E = '\033[1;31m'
Y = '\033[1;33m'
Z = '\033[1;31m'
X = '\033[1;33m'
Z1 = '\033[2;31m'
F = '\033[2;32m'
A = '\033[2;34m'
C = '\033[2;35m'
S = '\033[2;36m'
G = '\033[1;34m'
HH = '\033[1;34m'
M = '\x1b[1;37m'
B = '\033[2;36m'#سمائي
R = '\033[1;31m' #احمر

# Initialize global variables
good_hot, bad_hot, good_ig, bad_ig, check, mj = 0, 0, 0, 0, 0, 0
ids = []

# Open browser and show banner

print(f"""{C}
▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬
            
███████╗░█████╗░██████╗░██╗░░██╗░█████╗░░██████╗
██╔════╝██╔══██╗██╔══██╗██║░██╔╝██╔══██╗██╔════╝
█████╗░░███████║██████╔╝█████═╝░██║░░██║╚█████╗░
██╔══╝░░██╔══██║██╔══██╗██╔═██╗░██║░░██║░╚═══██╗
██║░░░░░██║░░██║██║░░██║██║░╚██╗╚█████╔╝██████╔╝
╚═╝░░░░░╚═╝░░╚═╝╚═╝░░╚═╝╚═╝░░╚═╝░╚════╝░╚═════╝░
          
•.¸.•*¯•.¸.•*¯  ☀ TLE : @Qrakr .¸.•*`•.¸¸.•* 

▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬
""")


url = "https://www.instagram.com/api/v1/web/accounts/account_recovery_send_ajax/"

print(f'{S}•••••••••••••••••••••••••••••••••••')

print('حط الايميل لتوصيل الريست 👇🏻')
print(f'{S}•••••••••••••••••••••••••••••••••••')
print('') # @Qrakr
EAN = input(f'{X} : حط الايميل')
os.system('clear')

payload = {
  'email_or_username': EAN,
  'jazoest': "21985"
}

headers = {
  'User-Agent': "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
  'sec-ch-ua': "\"Chromium\";v=\"139\", \"Not;A=Brand\";v=\"99\"",
  'x-ig-www-claim': "0",
  'x-web-session-id': "wpqs21:4p2oxh:fr620e",
  'sec-ch-ua-platform-version': "\"\"",
  'x-requested-with': "XMLHttpRequest",
  'sec-ch-ua-full-version-list': "\"Chromium\";v=\"139.0.7339.0\", \"Not;A=Brand\";v=\"99.0.0.0\"",
  'sec-ch-prefers-color-scheme': "dark",
  'x-csrftoken': "oHytELR8cICidXoxFwOWUO",
  'sec-ch-ua-platform': "\"Linux\"",
  'x-ig-app-id': "936619743392459",
  'sec-ch-ua-model': "\"\"",
  'sec-ch-ua-mobile': "?0",
  'x-instagram-ajax': "1027646615",
  'x-asbd-id': "359341",
  'origin': "https://www.instagram.com",
  'sec-fetch-site': "same-origin",
  'sec-fetch-mode': "cors",
  'sec-fetch-dest': "empty",
  'referer': "https://www.instagram.com/accounts/password/reset/",
  'accept-language': "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7",
  'Cookie': "csrftoken=oHytELR8cICidXoxFwOWUO; mid=aHNJyQABAAEIlJd36WaWByyzLL9e; datr=yUlzaC0tOWngp5oVByGvWYR6; ig_did=7087A5BC-7FFD-4427-917D-B8E8E803B028; ig_nrcb=1; ps_l=1; ps_n=1; dpr=2.1988937854766846; wd=891x1691"
}

EAN = requests.post(url, data=payload, headers=headers).text

if '''contact_point''' in EAN :
 print(f'{F}: تم توصيل الريست ✅')
 
else:
 
 print(f'{Z} لم يتم توصيل الريست ❌:')
