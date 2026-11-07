
import csv
import io
import json
import os
import re
import subprocess
import sys
import time
import unicodedata

REQUIRED={'requests':'requests','bs4':'beautifulsoup4','gspread':'gspread','google.oauth2':'google-auth'}
for module,pkg in REQUIRED.items():
    try:
        __import__(module)
    except ImportError:
        subprocess.check_call([sys.executable,'-m','pip','install',pkg])

from bs4 import BeautifulSoup
import gspread
from google.oauth2.service_account import Credentials
import requests

SPREADSHEET_ID='1Fpp_sJbGjuKxUAcWhZOM1YqHJJrUHTWE56lO4dthhak'
SHEET_NAME='community'
CONSTANTS_SID='1B8tX9VL2PcSJKyuHFVd2UT_8kYlY4ZdwHwg9MfWOPug'
COMMUNITY_CONSTANTS_SID='1Yv3GXnCIgEIbHL72EuZ-d5q_l-auPgddWi4Efa14jq0'
COMMUNITY_GID='182216'
SCRIPT_DIR=os.path.dirname(os.path.abspath(__file__))
SERVICE_ACCOUNT=os.path.join(SCRIPT_DIR,'service_account.json')
IS_GITHUB_ACTIONS=os.environ.get('GITHUB_ACTIONS')=='true'

DIFFICULTIES=['APPEND','MASTER','EXPERT','HARD']
DIFF_LABEL={'APPEND':'Append','MASTER':'Master','EXPERT':'Expert','HARD':'Hard'}
DIFF_ORDER={name:index for index,name in enumerate(DIFFICULTIES)}

S=requests.Session()
S.headers.update({'User-Agent':'Mozilla/5.0'})

def normalize_constant(value):
    value=str(value or '').strip().replace(',','.')
    return value if value else ''

def normalize_title(value):
    value=unicodedata.normalize('NFKC',str(value or '')).casefold()
    value=value.replace('’',"'").replace('　',' ')
    value=re.sub(r'\s+',' ',value)
    value=re.sub(r'[「」『』【】［］\[\]（）()]','',value)
    return value.strip()

def load_csv(url):
    response=S.get(url,timeout=60)
    response.raise_for_status()
    return list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))

def parse_source_rows(rows,source_name):
    constants={}
    title_ids={}
    for row in rows[1:]:
        if len(row)<7:
            continue
        title=row[0].strip()
        constant=normalize_constant(row[2])
        difficulty=row[5].strip().casefold()
        song_id=row[6].strip()
        try:
            numeric_id=int(song_id)
        except (ValueError,TypeError):
            continue
        if title:
            title_ids.setdefault(normalize_title(title),numeric_id)
        if constant and difficulty:
            constants[(difficulty,numeric_id)]=constant
    print(f'  Loaded {len(constants)} {source_name} constants and {len(title_ids)} title IDs.')
    return constants,title_ids

def load_constants():
    url=f'https://docs.google.com/spreadsheets/d/{CONSTANTS_SID}/gviz/tq?tqx=out:csv&sheet=Constants'
    return parse_source_rows(load_csv(url),'39s')

def load_community_constants():
    url=f'https://docs.google.com/spreadsheets/d/{COMMUNITY_CONSTANTS_SID}/export?format=csv&gid={COMMUNITY_GID}'
    return parse_source_rows(load_csv(url),'Not39s')

def get_constant(lookup,song_id,difficulty):
    if song_id is None:
        return ''
    try:
        return lookup.get((DIFF_LABEL.get(difficulty,difficulty).casefold(),int(song_id)),'')
    except (ValueError,TypeError):
        return ''

def strip_tags(value):
    return re.sub(r'\s+',' ',re.sub(r'<[^>]+>',' ',value).replace('&nbsp;',' ')).strip()

def clean(value):
    value=(value or '').strip()
    return value if value else '-'

def parse_difficulty(difficulty):
    url='https://pjsekai.com/?'+requests.utils.quote('楽曲難易度表'+difficulty)
    try:
        response=S.get(url,timeout=30)
        response.raise_for_status()
    except Exception as error:
        print(f'  [error] fetch {difficulty}: {error}')
        return []
    html=response.content.decode('utf-8',errors='replace')
    html=re.sub(r'</?font[^>]*>','',html,flags=re.I)
    rows=[]
    anchor_re=re.compile(r'id="[a-z]+(\d+)"',re.I)
    starts=[{'index':m.start(),'lvl':m.group(1)} for m in anchor_re.finditer(html)]
    starts.append({'index':len(html),'lvl':None})
    for index in range(len(starts)-1):
        level=starts[index]['lvl']
        section=html[starts[index]['index']:starts[index+1]['index']]
        tbody=re.search(r'<tbody>([\s\S]*?)</tbody>',section,re.I)
        if not tbody:
            continue
        for match in re.finditer(r'<tr>([\s\S]*?)</tr>',tbody.group(1),re.I):
            cells=re.findall(r'<td[^>]*>([\s\S]*?)</td>',match.group(1),re.I)
            if len(cells)<2:
                continue
            title_match=re.search(r'<a[^>]*>([\s\S]*?)</a>',cells[0],re.I)
            title=strip_tags(title_match.group(1) if title_match else cells[0])
            if not title:
                continue
            judgment=''
            history=''
            memo=re.search(r'<div[^>]*class="memo1"[^>]*>([\s\S]*?)</div>',cells[1],re.I)
            if memo:
                memo_html=memo.group(1)
                memo2=re.search(r'<span[^>]*class="memo2"[^>]*>([\s\S]*?)</span>',memo_html,re.I)
                if memo2:
                    history=strip_tags(memo2.group(1))
                    memo_html=memo_html.replace(memo2.group(0),'')
                judgment=strip_tags(memo_html)
            else:
                judgment=strip_tags(cells[1])
            elements=', '.join(strip_tags(item) for item in re.findall(r'<li[^>]*>([\s\S]*?)</li>',cells[2] if len(cells)>2 else '',re.I) if strip_tags(item))
            comments=strip_tags(cells[3]) if len(cells)>3 else ''
            rows.append([clean(title),clean(judgment),clean(elements),clean(comments),difficulty,clean(level),clean(history)])
    return rows

def load_39s_song_ids(gc):
    worksheet=gc.open_by_key(SPREADSHEET_ID).worksheet('jp_musics')
    mapping={}
    for row in worksheet.get('A:E')[1:]:
        if len(row)<5:
            continue
        title=row[0].strip()
        try:
            song_id=int(row[4].strip())
        except (ValueError,TypeError):
            continue
        if title:
            mapping.setdefault(normalize_title(title),song_id)
    print(f'  Loaded {len(mapping)} song IDs from jp_musics.')
    return mapping

def resolve_song_id(title,*mappings):
    key=normalize_title(title)
    for mapping in mappings:
        if key in mapping:
            return mapping[key]
    return None

def sort_rows(rows):
    def key(row):
        song_id=row[7]
        return (song_id is None,song_id if song_id is not None else float('inf'),DIFF_ORDER.get(row[4],99),row[0].casefold())
    return sorted(rows,key=key)

def JUDGMENT():
    print('=== COMMUNITY scraper started ===')
    all_rows=[]
    for index,difficulty in enumerate(DIFFICULTIES,1):
        rows=parse_difficulty(difficulty)
        print(f'  [{index}/{len(DIFFICULTIES)}] {difficulty}: {len(rows)} rows')
        all_rows.extend(rows)
        time.sleep(0.4)
    if not all_rows:
        raise RuntimeError('No data scraped. Google Sheets was not modified.')

    scopes=['https://www.googleapis.com/auth/spreadsheets','https://www.googleapis.com/auth/drive']
    if os.environ.get('GCP_SA_KEY'):
        credentials=Credentials.from_service_account_info(json.loads(os.environ['GCP_SA_KEY']),scopes=scopes)
    else:
        credentials=Credentials.from_service_account_file(SERVICE_ACCOUNT,scopes=scopes)
    gc=gspread.authorize(credentials)

    print('Loading 39s constants...')
    constants_39s,ids_39s=load_constants()
    print('Loading Not39s constants...')
    constants_not39s,ids_not39s=load_community_constants()
    print('Loading song IDs...')
    ids_jp=load_39s_song_ids(gc)

    output=[]
    for row in all_rows:
        title=row[0]
        difficulty=row[4]
        song_id=resolve_song_id(title,ids_jp,ids_39s,ids_not39s)
        constant_39s=get_constant(constants_39s,song_id,difficulty)
        constant_not39s=get_constant(constants_not39s,song_id,difficulty)
        output.append(row+[song_id,constant_39s or '-',constant_not39s or '-'])

    output=sort_rows(output)
    header=['title','judgment','elements','comments','difficulty','lvl','history','id','39sConstant','Not39s']
    final_output=[header]+[[*row[:7],row[7] if row[7] is not None else '-',row[8],row[9]] for row in output]

    spreadsheet=gc.open_by_key(SPREADSHEET_ID)
    try:
        worksheet=spreadsheet.worksheet(SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        worksheet=spreadsheet.add_worksheet(title=SHEET_NAME,rows=max(1000,len(final_output)+10),cols=len(header))

    worksheet.clear()
    worksheet.resize(rows=max(1000,len(final_output)+10),cols=len(header))
    worksheet.update(range_name='A1',values=final_output,value_input_option='RAW')

    matched=sum(row[7]!='-' for row in final_output[1:])
    matched_39s=sum(row[8]!='-' for row in final_output[1:])
    matched_not39s=sum(row[9]!='-' for row in final_output[1:])
    print(f'=== Done. {len(output)} rows written. ===')
    print(f'  IDs matched: {matched}/{len(output)}')
    print(f'  39s constants matched: {matched_39s}')
    print(f'  Not39s constants matched: {matched_not39s}')

if __name__=='__main__':
    try:
        JUDGMENT()
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        if not IS_GITHUB_ACTIONS:
            input('Press Enter to exit...')