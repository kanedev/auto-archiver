import gspread
import youtube_dl
from pathlib import Path
import sys
import datetime
import boto3
import os
from dotenv import load_dotenv
from botocore.errorfactory import ClientError
import argparse

load_dotenv()

def col_to_index(col):
    col = list(col)
    ndigits = len(col)
    alphabet = ' ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    v = 0
    i = ndigits - 1

    for digit in col:
        index = alphabet.find(digit)
        v += (26 ** i) * index
        i -= 1

    return v - 1

def download_vid(url, s3_client, check_if_exists=False):
    ydl_opts = {'outtmpl': 'tmp/%(id)s.%(ext)s', 'quiet': False}
    ydl = youtube_dl.YoutubeDL(ydl_opts)

    if check_if_exists:
        info = ydl.extract_info(url, download=False)

        if 'entries' in info:
            if len(info['entries']) > 1:
                raise Exception('ERROR: Cannot archive channels or pages with multiple videos')

            filename = ydl.prepare_filename(info['entries'][0])
        else:
            filename = ydl.prepare_filename(info)
                
        key = filename.split('/')[1]
        cdn_url = 'https://{}.{}.cdn.digitaloceanspaces.com/{}'.format(os.getenv('DO_BUCKET'), os.getenv('DO_SPACES_REGION'), key)

        try:
            s3_client.head_object(Bucket=os.getenv('DO_BUCKET'), Key=key)
            
            # file exists
            return (cdn_url, 'already archived')

        except ClientError:
            pass

    # sometimes this results in a different filename, so do this again
    info = ydl.extract_info(url, download=True)

    if 'entries' in info:
        if len(info['entries']) > 1:
            raise Exception('ERROR: Cannot archive channels or pages with multiple videos')

        filename = ydl.prepare_filename(info['entries'][0])
    else:
        filename = ydl.prepare_filename(info)

    if not os.path.exists(filename):
        filename = filename.split('.')[0] + '.mkv'

    key = filename.split('/')[1]
    cdn_url = 'https://{}.{}.cdn.digitaloceanspaces.com/{}'.format(os.getenv('DO_BUCKET'), os.getenv('DO_SPACES_REGION'), key)

    with open(filename, 'rb') as f:
        s3_client.upload_fileobj(f, Bucket=os.getenv('DO_BUCKET'), Key=key, ExtraArgs={'ACL': 'public-read'})

    os.remove(filename)

    return (cdn_url, 'success')

def main():
    parser = argparse.ArgumentParser(description="Automatically use youtube-dl to download media from a Google Sheet")
    parser.add_argument("--sheet", action="store", dest="sheet")
    parser.add_argument('--streaming', dest='streaming', action='store_true')
    parser.add_argument('--all-worksheets', dest='all_worksheets', action='store_true')
    parser.add_argument('--url-col', dest='url', action='store')
    parser.add_argument('--archive-col', dest='archive', action='store')
    parser.add_argument('--date-col', dest='date', action='store')
    parser.add_argument('--status-col', dest='status', action='store') 
    args = parser.parse_args()

    def update_sheet(wks, row, status, url):
        update = [{
            'range': args.status + str(row),
            'values': [[status]]
        }, {
            'range': args.date + str(row),
            'values': [[datetime.datetime.now().isoformat()]]
        }, {
            'range': args.archive + str(row),
            'values': [[url]]
        }]

        if url is None:
            update = update[:-1]

        wks.batch_update(update)

    print("Opening document " + args.sheet)

    gc = gspread.service_account()
    sh = gc.open(args.sheet)
    n_worksheets = len(sh.worksheets()) if args.all_worksheets else 1

    s3_client = boto3.client('s3',
            region_name=os.getenv('DO_SPACES_REGION'),
            endpoint_url='https://{}.digitaloceanspaces.com'.format(os.getenv('DO_SPACES_REGION')),
            aws_access_key_id=os.getenv('DO_SPACES_KEY'),
            aws_secret_access_key=os.getenv('DO_SPACES_SECRET'))

    # loop through worksheets to check
    for ii in range(n_worksheets):
        print("Opening worksheet " + str(ii))
        wks = sh.get_worksheet(ii)
        values = wks.get_all_values()

        # loop through rows in worksheet
        for i in range(2, len(values)+1):
            v = values[i-1]

            url_index = col_to_index(args.url)

            if v[url_index] != "" and v[col_to_index(args.status)] == "":
                print(v[col_to_index(args.url)])

                try:
                    ydl_opts = {'outtmpl': 'tmp/%(id)s.%(ext)s', 'quiet': False}
                    ydl = youtube_dl.YoutubeDL(ydl_opts)
                    info = ydl.extract_info(v[url_index], download=False)

                    if args.streaming and 'is_live' in info and info['is_live']:
                        wks.update(args.status + str(i), 'Recording stream')
                        cdn_url, status = download_vid(v[url_index], s3_client)
                        update_sheet(wks, i, status, cdn_url)
                        sys.exit()
                    elif not args.streaming and ('is_live' not in info or not info['is_live']):
                        cdn_url, status = download_vid(v[url_index], s3_client, check_if_exists=True)
                        update_sheet(wks, i, status, cdn_url)
                        
                except:
                    # if any unexpected errors occured, log these into the Google Sheet
                    t, value, traceback = sys.exc_info()
                    update_sheet(wks, i, str(value), None)

if __name__ == "__main__":
    main()