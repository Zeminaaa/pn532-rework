import socket
import time
import json
from templates import HTML_PARTS, TEST_HTML_PARTS
from logger import get_logger

logger = get_logger(__name__)

_CAPTIVE_URLS = (
    '/generate_204',
    '/gen_204',
    '/hotspot-detect.html',
    '/library/test/success.html',
    '/connectivity-check.html',
    '/ncsi.txt',
    '/redirect',
    '/canonical.html',
)

class DashboardServer:
    """Non-blocking WebServer for the Master Station."""

    def __init__(self, get_state_cb, set_mode_cb, wipe_card_cb) -> None:
        self.get_state = get_state_cb
        self.set_mode = set_mode_cb
        self.wipe_card = wipe_card_cb
        
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('0.0.0.0', 80))
        self.sock.listen(3)
        self.sock.setblocking(False)
        logger.info('[WEB] Dashboard listening on :80')

    def sync_state(self, force: bool = False) -> None:
        """Called to flush pending data to disk. (Unused placeholder if no SD present)."""
        pass

    def handle_request(self) -> None:
        try:
            conn, addr = self.sock.accept()
        except OSError:
            return

        try:
            conn.settimeout(0.3)
            raw = self._recv_request(conn)
            if not raw:
                return

            first = raw.split(b'\r\n', 1)[0].decode('utf-8', 'ignore')
            parts = first.split(' ')
            if len(parts) >= 2:
                method = parts[0]
                path = parts[1]
                body = self._body_from_raw(raw)
                self._dispatch(conn, method, path, body)
        except Exception as err:
            logger.error(f'[WEB] Chyba zpracovani: {err}')
        finally:
            try:
                conn.close()
            except Exception:
                pass

    def _recv_request(self, conn) -> bytes:
        raw = b''
        while True:
            try:
                chunk = conn.recv(512)
                if not chunk:
                    break
                raw += chunk
                if b'\r\n\r\n' in raw:
                    content_length = self._content_length(raw)
                    header_end = raw.find(b'\r\n\r\n') + 4
                    if len(raw) >= header_end + content_length:
                        break
                if len(raw) > 8192:
                    break
            except OSError:
                break
        return raw

    def _content_length(self, raw: bytes) -> int:
        headers = raw.split(b'\r\n\r\n', 1)[0].decode('utf-8', 'ignore')
        for line in headers.split('\r\n'):
            lower = line.lower()
            if lower.startswith('content-length:'):
                try:
                    return int(line.split(':', 1)[1].strip())
                except ValueError:
                    return 0
        return 0

    def _body_from_raw(self, raw: bytes) -> str:
        try:
            parts = raw.split(b'\r\n\r\n', 1)
            if len(parts) == 2:
                return parts[1].decode('utf-8', 'ignore')
        except Exception:
            pass
        return ""

    def _dispatch(self, conn, method: str, path: str, body: str) -> None:
        base = path.split('?')[0]
        if method == 'GET':
            if base == '/' or base in _CAPTIVE_URLS:
                self._serve_dashboard(conn)
            elif base == '/status':
                self._serve_status(conn)
            elif base == '/test':
                self._serve_test_page(conn)
            elif base == '/ping':
                conn.send(b"HTTP/1.1 204 No Content\r\n\r\n")
            elif base == '/start-sync':
                self._serve_start_sync(conn, path)
            elif base == '/resume-last':
                self._serve_resume_last(conn)
            elif base == '/stop':
                self._serve_stop(conn)
            elif base == '/export.csv':
                self._serve_csv(conn)
            else:
                self._redirect(conn, '/')
        elif method == 'POST':
            if base == '/name':
                self._serve_name(conn, body)
            else:
                self._serve_404(conn)
        else:
            self._serve_404(conn)

    def _serve_dashboard(self, conn) -> None:
        conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\r\n")
        for chunk in HTML_PARTS:
            conn.send(chunk.encode('utf-8'))

    def _serve_test_page(self, conn) -> None:
        conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\r\n")
        for chunk in TEST_HTML_PARTS:
            conn.send(chunk.encode('utf-8'))

    def _serve_status(self, conn) -> None:
        state = self.get_state()
        j = self._make_json(state)
        conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\n\r\n")
        conn.send(j.encode('utf-8'))

    def _serve_start_sync(self, conn, path: str) -> None:
        try:
            n = 8
            if '?n=' in path:
                n = int(path.split('?n=')[1].split('&')[0])
            self.set_mode('SYNCING', n)
            conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"ok\":true}")
        except Exception:
            conn.send(b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\n\r\n{\"ok\":false,\"error\":\"Neplatny pocet stanic\"}")

    def _serve_resume_last(self, conn) -> None:
        conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"ok\":true}")

    def _serve_stop(self, conn) -> None:
        self.set_mode('IDLE', 0)
        conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"ok\":true}")

    def _serve_name(self, conn, body: str) -> None:
        try:
            payload = json.loads(body)
            read_id = int(payload.get('id', 0))
            name = str(payload.get('name', '')).strip()
            state = self.get_state()
            reads = state.get('chip_readings', [])
            if 0 <= read_id < len(reads):
                reads[read_id]['name'] = name
        except Exception as ex:
            logger.error(f'[WEB] Chyba _serve_name: {ex}')
        conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n{\"ok\":true}")

    def _serve_csv(self, conn) -> None:
        state = self.get_state()
        reads = state.get('chip_readings', [])
        visible_count = state.get('num_stations', 0)
        csv_str = self._csv_from_rows(reads, visible_count)
        conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: text/csv; charset=utf-8\r\nContent-Disposition: attachment; filename=results.csv\r\n\r\n")
        conn.send(csv_str.encode('utf-8'))

    def _csv_from_rows(self, reads: list, visible_count: int) -> str:
        headers = ['jmeno', 'uid']
        for index in range(visible_count):
            headers.append(f'S{index + 1}')
        headers.extend(['vysledek_s', 'cas_cteni'])
        lines = [','.join(headers)]
        for idx, row in enumerate(reads):
            res = self._result_seconds(row, visible_count)
            res_str = "" if res is None else str(res)
            values = [row.get('name', ''), row.get('uid', '')]
            times = row.get('times', [])
            for index in range(visible_count):
                values.append(times[index] if index < len(times) else '0')
            values.append(res_str)
            values.append(row.get('ts', ''))
            lines.append(','.join(self._csv_cell(value) for value in values))
        return '\r\n'.join(lines) + '\r\n'

    def _csv_cell(self, value) -> str:
        text = str(value)
        if '"' in text:
            text = text.replace('"', '""')
        if ',' in text or '"' in text or '\n' in text or '\r' in text:
            text = '"' + text + '"'
        return text

    def _result_seconds(self, reading: dict, station_count: int):
        times = reading.get('times', [])
        if station_count < 1 or not times:
            return None
        try:
            start = int(times[0])
        except (ValueError, TypeError):
            return None
        master_time = reading.get('master_time', '')
        try:
            finish = int(master_time)
        except (ValueError, TypeError):
            finish = None
        if finish is None or finish <= 0:
            finish_index = station_count - 1
            if finish_index < 0 or len(times) <= finish_index:
                return None
            try:
                finish = int(times[finish_index])
            except (ValueError, TypeError):
                return None
        if start <= 0 or finish <= 0:
            return None
        if finish < start:
            finish += 65536
        return finish - start

    def _table_rows_str(self, reads: list, station_count: int) -> str:
        rows = []
        for idx, r in enumerate(reads):
            res = self._result_seconds(r, station_count)
            res_str = str(res) if res is not None else "null"
            name = self._esc(r.get('name', ''))
            uid = self._esc(r.get('uid', ''))
            ts = self._esc(r.get('ts', ''))
            row_id = r.get('id', idx)
            times = r.get('times', [])
            s_fields = []
            for i in range(station_count):
                val = times[i] if i < len(times) else "0"
                s_fields.append(f'"S{i + 1}":"{val}"')
            s_fields_str = ",".join(s_fields)
            if s_fields_str:
                s_fields_str = "," + s_fields_str
            rows.append(f'{{"id":{row_id},"name":"{name}","uid":"{uid}","read_at":"{ts}","result_seconds":{res_str}{s_fields_str}}}')
        return "[" + ",".join(rows) + "]"

    def _make_json(self, state: dict) -> str:
        mode = state.get('mode', 'IDLE')
        n_st = state.get('num_stations', 0)
        s_ids = list(state.get('synced_ids', []))
        reads = state.get('chip_readings', [])
        log = state.get('log', [])

        s_ids_str = "[" + ",".join([str(x) for x in s_ids]) + "]"
        
        reads_json = []
        for idx, r in enumerate(reads):
            uid = r['uid']
            ts = r['ts']
            m_time = r['master_time']
            name = self._esc(r.get('name', ''))
            row_id = r.get('id', idx)
            times_arr = "[" + ",".join([f'"{x}"' for x in r['times']]) + "]"
            reads_json.append(f'{{"id":{row_id},"name":"{name}","uid":"{uid}","ts":"{ts}","master_time":"{m_time}","times":{times_arr}}}')
        reads_str = "[" + ",".join(reads_json) + "]"

        table_rows_str = self._table_rows_str(reads, n_st)
        log_str = "[" + ",".join([f'"{self._esc(x)}"' for x in log]) + "]"

        return f'{{"mode":"{mode}","num_stations":{n_st},"total_station_count":{n_st},"visible_station_count":{n_st},"synced_ids":{s_ids_str},"chip_readings":{reads_str},"table_rows":{table_rows_str},"log":{log_str}}}'

    def _esc(self, s: str) -> str:
        return s.replace('"', '\\"').replace('\n', ' ').replace('\r', ' ')

    def _redirect(self, conn, loc: str) -> None:
        conn.send(f"HTTP/1.1 303 See Other\r\nLocation: {loc}\r\n\r\n".encode('utf-8'))

    def _serve_404(self, conn) -> None:
        conn.send(b"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\n\r\n404 Not Found")
