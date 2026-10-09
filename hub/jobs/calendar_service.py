# Copied verbatim from v1 (server/services/calendar_service.py). Finds token.pickle via the cwd,
# which the calendar job sets to ~/bigscreen-state/secrets.
import os
import pickle
import re
import time
import datetime
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

class CalendarService:
    def __init__(self, config):
        self.config = config
        self.creds = None
        self.service = None
        self.color_map = {}  # Map event colorId to RGB tuple
        
        self._authenticate()
        # Fetch event colors only (not calendar colors)
        self._fetch_colors()
        
    def _authenticate(self):
        # Look in current directory, server directory, or relative to this file
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) # server/
        root_dir = os.path.dirname(base_dir) # project root
        
        paths_to_try = [
            'token.pickle',
            os.path.join(os.getcwd(), 'token.pickle'),
            os.path.join(os.getcwd(), 'server', 'token.pickle'),
            os.path.join(base_dir, 'token.pickle'),
            os.path.join(root_dir, 'token.pickle'),
        ]
        
        token_path = None
        for p in paths_to_try:
            if os.path.exists(p):
                token_path = p
                break
        
        if token_path:
            print(f"Loading token from: {token_path}")
            try:
                with open(token_path, 'rb') as token:
                    self.creds = pickle.load(token)
            except Exception as e:
                print(f"Error reading token: {e}")
        else:
            print(f"Token not found. Please put token.pickle in server directory.")
                
        # Refresh token if expired or close to expiring (proactive refresh)
        if self.creds and self.creds.refresh_token:
            # Check if token is expired or will expire soon (within 5 minutes)
            needs_refresh = self.creds.expired
            if not needs_refresh and hasattr(self.creds, 'expiry') and self.creds.expiry:
                time_until_expiry = self.creds.expiry.timestamp() - time.time()
                needs_refresh = time_until_expiry < 300  # Refresh if expiring within 5 minutes
            
            if needs_refresh:
                print("Token expired or expiring soon, attempting to refresh...")
                try:
                    self.creds.refresh(Request())
                    with open(token_path, 'wb') as token:
                        pickle.dump(self.creds, token)
                    print("Token refreshed successfully and saved.")
                except Exception as e:
                    error_str = str(e)
                    if 'invalid_grant' in error_str or 'revoked' in error_str.lower():
                        print(f"ERROR: Token refresh failed - refresh token has been expired or revoked.")
                        print(f"       This usually happens if:")
                        print(f"       1. OAuth app is in 'Testing' mode (refresh tokens expire after 7 days)")
                        print(f"       2. Refresh token was revoked in Google Account settings")
                        print(f"       3. OAuth app was deleted or modified")
                        print(f"       Solution: Re-run auth_google.py to get a new refresh token")
                        print(f"       Token file location: {token_path}")
                        # Clear invalid credentials
                        self.creds = None
                    else:
                        print(f"Error refreshing token: {e}")
                        # Don't clear creds on other errors - might be temporary network issue
        
        if self.creds and self.creds.valid:
            self.service = build('calendar', 'v3', credentials=self.creds)
        else:
            print("Calendar Service Error: Invalid credentials. Run auth_google.py to re-authenticate.")
    
    def _fetch_colors(self):
        """Fetch event color definitions and map them to RGB values"""
        if not self.service:
            return
        
        try:
            colors_result = self.service.colors().get().execute()
            event_colors = colors_result.get('event', {})
            
            # Google Calendar API returns colors in hex format (e.g., "#a4bdfc")
            # Convert hex to RGB tuple for EVENT colors only
            for color_id, color_info in event_colors.items():
                background_hex = color_info.get('background', '#1d1d1d')
                # Remove '#' and convert hex to RGB
                if background_hex.startswith('#'):
                    hex_color = background_hex[1:]
                    r = int(hex_color[0:2], 16)
                    g = int(hex_color[2:4], 16)
                    b = int(hex_color[4:6], 16)
                    # Store as string key for consistent lookup
                    self.color_map[str(color_id)] = (r, g, b)
                    print(f"  Mapped event colorId '{color_id}' -> RGB ({r}, {g}, {b}) from hex {background_hex}")
            
            print(f"Loaded {len(self.color_map)} event colors")
        except Exception as e:
            print(f"Error fetching event colors: {e}")
            # Use default colors if fetch fails
            self._set_default_colors()
    
    def _set_default_colors(self):
        """Set default color mappings if API fetch fails"""
        # Default Google Calendar colors (approximate RGB values)
        default_colors = {
            '1': (164, 189, 252),  # Lavender
            '2': (122, 231, 191),   # Sage
            '3': (219, 173, 255),  # Grape (Purple)
            '4': (255, 136, 124),  # Flamingo
            '5': (251, 215, 91),   # Banana
            '6': (255, 184, 120), # Tangerine
            '7': (70, 214, 219),  # Peacock
            '8': (225, 225, 225), # Graphite
            '9': (84, 132, 237),  # Blueberry
            '10': (81, 183, 73),  # Basil
            '11': (220, 33, 39),  # Tomato
        }
        self.color_map = default_colors
    

    def fetch_events(self):
        if not self.service:
            return []

        all_events = []
        calendar_ids = self.config.get("modules", {}).get("calendar", {}).get("calendars", ["primary"])
        cc_cfg = self.config.get("modules", {}).get("client_calls_panel", {})
        lookahead_days = cc_cfg.get("lookahead_days", 30)
        lookback_days = cc_cfg.get("past_lookback_days", 30)
        now_dt = datetime.datetime.utcnow()
        now = now_dt.isoformat() + 'Z'
        end_future = (now_dt + datetime.timedelta(days=lookahead_days)).isoformat() + 'Z'
        start_past = (now_dt - datetime.timedelta(days=lookback_days)).isoformat() + 'Z'

        for cal_id in calendar_ids:
            try:
                # Future: now -> +lookahead_days. Paginate so we get ALL events in the window.
                future_events = []
                page_token = None
                while True:
                    events_result = self.service.events().list(
                        calendarId=cal_id, timeMin=now, timeMax=end_future,
                        maxResults=250, singleEvents=True, orderBy='startTime',
                        pageToken=page_token
                    ).execute()
                    page = events_result.get('items', [])
                    for e in page:
                        e['_source_calendar'] = cal_id
                        future_events.append(e)
                    page_token = events_result.get('nextPageToken')
                    if not page_token:
                        break
                all_events.extend(future_events)

                # Past: -lookback_days -> now (client-calls past panel). Paginate to get ALL past
                # events (API returns oldest-first, so a single 50-result page would miss this week).
                if lookback_days > 0:
                    past_events = []
                    page_token = None
                    while True:
                        past_result = self.service.events().list(
                            calendarId=cal_id, timeMin=start_past, timeMax=now,
                            maxResults=250, singleEvents=True, orderBy='startTime',
                            pageToken=page_token
                        ).execute()
                        page = past_result.get('items', [])
                        for e in page:
                            e['_source_calendar'] = cal_id
                            past_events.append(e)
                        page_token = past_result.get('nextPageToken')
                        if not page_token:
                            break
                    all_events.extend(past_events)
            except Exception as e:
                error_str = str(e)
                if 'invalid_grant' in error_str or 'revoked' in error_str.lower():
                    print(f"Error fetching calendar {cal_id}: Token expired or revoked. Re-authentication required.")
                    self._authenticate()
                else:
                    print(f"Error fetching calendar {cal_id}: {e}")

        # Deduplicate by event id (same event can appear from multiple calendars or at past/future boundary)
        seen_ids = {}
        deduped = []
        for e in all_events:
            eid = e.get('id')
            if eid and eid not in seen_ids:
                seen_ids[eid] = True
                deduped.append(e)
            elif not eid:
                deduped.append(e)
        all_events = deduped
        all_events.sort(key=lambda x: x['start'].get('dateTime', x['start'].get('date')))
        return self._process_events(all_events)

    def _process_events(self, events):
        processed = []
        ep = self.config.get("modules", {}).get("calendar", {}).get("event_processing", {})
        rules = ep.get("title_replacements", [])
        exclusions = ep.get("exclude_events_containing", [])
        # New: title-keyword color rules. Each rule is {match_pattern, color: [r,g,b]}.
        # First match wins. Applied AFTER Google's colorId logic so it overrides.
        # We pre-compile regexes once per fetch to skip bad ones quickly.
        color_rule_specs = ep.get("event_color_rules", []) or []
        compiled_color_rules = []
        for rule in color_rule_specs:
            pat = rule.get("match_pattern")
            col = rule.get("color")
            if not pat or not isinstance(col, (list, tuple)) or len(col) != 3:
                continue
            try:
                compiled_color_rules.append((re.compile(pat, re.IGNORECASE), tuple(int(c) for c in col)))
            except (re.error, ValueError, TypeError):
                continue

        # Status overlay rules: a marker event (e.g. titled "done", "postponed",
        # "noshow") placed on top of an existing event in Google Calendar marks
        # that event as having a status. The marker matches by exact start+end.
        # First rule that matches the marker title (in config order) wins. The
        # marker event itself is hidden from the output; only its overlay info
        # rides along on the underlying event as `_status_overlay`.
        cc_cfg = self.config.get("modules", {}).get("client_calls_panel", {})
        status_rule_specs = cc_cfg.get("status_overlay_rules", []) or []
        compiled_status_rules = []
        for rule in status_rule_specs:
            pat = rule.get("match_pattern")
            if not pat:
                continue
            mode = (rule.get("display_mode") or "label_override").strip().lower()
            if mode not in ("strikethrough", "label_override"):
                mode = "label_override"
            try:
                col = rule.get("color") or [200, 200, 200]
                col = [max(0, min(255, int(c))) for c in (list(col) + [0, 0, 0])[:3]]
                compiled_status_rules.append({
                    "regex": re.compile(pat, re.IGNORECASE),
                    "name": rule.get("name") or pat,
                    "display_mode": mode,
                    "label_text": rule.get("label_text") or "",
                    "color": col,
                })
            except (re.error, ValueError, TypeError):
                continue

        # Build (start, end) -> matched rule map by scanning the raw events.
        # Done BEFORE exclusion so a marker like "done" is detectable even if
        # the user later adds it to exclude_events_containing.
        overlay_slots = {}
        overlay_event_ids = set()
        if compiled_status_rules:
            for e in events:
                title_raw = (e.get("summary") or "").strip()
                if not title_raw:
                    continue
                for rule in compiled_status_rules:
                    if rule["regex"].search(title_raw):
                        start = (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date")
                        end = (e.get("end") or {}).get("dateTime") or (e.get("end") or {}).get("date")
                        if start and end:
                            slot_key = (start, end)
                            if slot_key not in overlay_slots:
                                overlay_slots[slot_key] = rule
                        eid = e.get("id")
                        if eid:
                            overlay_event_ids.add(eid)
                        break

        print(f"[CALENDAR] Processing {len(events)} events for colors...")
        events_with_colorid = 0
        events_with_rule_color = 0
        events_with_status_overlay = 0

        for e in events:
            # Skip marker events themselves — they are metadata, not for display.
            if compiled_status_rules and e.get("id") in overlay_event_ids:
                continue

            title = e.get('summary', 'No Title')

            should_exclude = False
            for exclude_str in exclusions:
                if exclude_str.lower() in title.lower():
                    should_exclude = True
                    break
            if should_exclude:
                continue
            
            for rule in rules:
                pattern = rule.get('match_pattern')
                replacement = rule.get('replacement')
                if pattern and replacement:
                    try:
                        title = re.sub(pattern, replacement, title)
                    except re.error:
                        pass
            
            e['summary_clean'] = title.strip()
            
            # Custom event color (right-click event in Google Calendar → set color).
            # API returns colorId only when the user has set an explicit color on an event they own.
            # For calendars shared TO this account (e.g. work calendar shared to personal), Google
            # does not return colorId for those events. We keep it when present for calendar.json.
            color_id = e.get('colorId')
            if color_id is not None:
                events_with_colorid += 1
                # Ensure colorId is preserved as string for consistent JSON and client use
                if not isinstance(color_id, str):
                    e['colorId'] = str(color_id)
                color_id_str = str(color_id)
                if color_id_str in self.color_map:
                    # Darken the color for better visibility on LED matrix (multiply by 0.4)
                    original_rgb = self.color_map[color_id_str]
                    darkened_rgb = (
                        int(original_rgb[0] * 0.4),
                        int(original_rgb[1] * 0.4),
                        int(original_rgb[2] * 0.4)
                    )
                    e['_event_color'] = darkened_rgb
                    print(f"  ✓ Event '{title[:40]}' -> colorId '{color_id_str}' -> RGB {darkened_rgb} (darkened from {original_rgb})")
                else:
                    # ColorId exists but not in our map - use default
                    print(f"  ✗ Event '{title[:40]}' has colorId '{color_id_str}' but NOT in color_map!")
                    print(f"    Available colorIds: {list(self.color_map.keys())}")
                    e['_event_color'] = (0, 0, 100)
            else:
                # No event colorId - event is using calendar's default color
                # Use default dark blue (no calendar color fallback per user request)
                e['_event_color'] = (0, 0, 100)

            # Title-keyword color rules (applied last, so they override colorId). The
            # cleaned title is matched against each rule's regex (case-insensitive); the
            # first hit replaces _event_color. We darken by 0.4× to match the visual
            # weight of Google's colorId mapping above.
            if compiled_color_rules:
                for pattern, rgb in compiled_color_rules:
                    if pattern.search(title):
                        e['_event_color'] = (
                            int(rgb[0] * 0.4),
                            int(rgb[1] * 0.4),
                            int(rgb[2] * 0.4),
                        )
                        events_with_rule_color += 1
                        break

            # Status overlay: if a marker event sits on the same start+end slot,
            # attach its rule info so the client renderer can strike-through the
            # name or replace the call-type label. The marker event itself was
            # filtered out above; this is purely a per-event annotation.
            if overlay_slots:
                ev_start = (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date")
                ev_end = (e.get("end") or {}).get("dateTime") or (e.get("end") or {}).get("date")
                if ev_start and ev_end:
                    rule = overlay_slots.get((ev_start, ev_end))
                    if rule:
                        e["_status_overlay"] = {
                            "name": rule["name"],
                            "display_mode": rule["display_mode"],
                            "label_text": rule["label_text"],
                            "color": list(rule["color"]),
                        }
                        events_with_status_overlay += 1

            processed.append(e)

        print(f"[CALENDAR] Color summary: {events_with_colorid}/{len(processed)} events have custom color (colorId sent with event data)")
        if compiled_color_rules:
            print(f"[CALENDAR] Title-keyword color rules: {events_with_rule_color}/{len(processed)} events matched a rule")
        if compiled_status_rules:
            print(f"[CALENDAR] Status overlays: {events_with_status_overlay}/{len(processed)} events got a status marker (markers themselves hidden)")
        return processed
