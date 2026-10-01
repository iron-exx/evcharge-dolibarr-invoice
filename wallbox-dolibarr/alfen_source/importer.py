"""Import abgeschlossener Alfen-Transaktionen in den SessionManager.

Abgebildet auf die OCPP-Maschinerie, statt eine zweite zu bauen: die
Transaktions-ID der Wallbox wird zum `ocpp_start_timestamp`, die Steckdose zum
Connector. Damit gelten unverändert die dort getesteten Eigenschaften —
Idempotenz, Plausibilitätsprüfung, Zähler-Fallback und die Abrechnungsschranke
für private Karten.

Die Idempotenz ist hier nicht optional: das Transaktions-Log der Wallbox wird
bei jedem Durchlauf komplett gelesen, also wird jeder Vorgang viele Male
gesehen. Ohne sie würde jede Ladung endlos neu abgerechnet.
"""
import logging
import re
from typing import Callable, Dict, List, Optional

_LOGGER = logging.getLogger(__name__)

_SOCKET_NUM = re.compile(r'(\d+)')


def _connector_of(socket: str) -> int:
    match = _SOCKET_NUM.search(str(socket or ''))
    return int(match.group(1)) if match else 1


def import_transactions(session_manager, transactions, wallbox_id: str,
                        whitelist=None, charge_point_id: str = 'alfen_http',
                        on_tag_seen: Optional[Callable[[str], object]] = None) -> Dict[str, int]:
    """Transaktionen übernehmen. Gibt eine Zählung zurück.

    `already_known` ist der Normalfall, nicht ein Fehler: beim zweiten Lesen
    desselben Logs ist alles bereits bekannt.
    """
    result = {'imported': 0, 'already_known': 0, 'unauthorized': 0,
              'not_billable': 0, 'errors': 0}

    for tx in transactions or []:
        try:
            tag = str(tx['tag']).strip().upper()
            socket = tx.get('socket') or 'Socket 1'

            # Lernmodus zuerst: auch eine nicht autorisierte Karte muss sichtbar
            # werden, sonst kann der Admin sie nie freischalten.
            if on_tag_seen is not None:
                try:
                    on_tag_seen(tag)
                except Exception as exc:
                    _LOGGER.warning("Lernmodus-Hook fehlgeschlagen: %s", exc)

            if not session_manager.is_rfid_authorized(tag, list(whitelist or [])):
                result['unauthorized'] += 1
                continue

            session_id = session_manager.start_ocpp_transaction(
                rfid_hex=tag,
                wallbox_id=wallbox_id,
                charge_point_id=charge_point_id,
                connector_id=_connector_of(socket),
                meter_start_kwh=float(tx['start_kwh']),
                start_time=str(tx['start_time']),
                ocpp_start_timestamp=str(tx['transaction_id']),
            )

            completed = session_manager.stop_ocpp_transaction(
                transaction_id=session_id,
                charge_point_id=charge_point_id,
                meter_stop_kwh=float(tx['end_kwh']),
                end_time=str(tx['end_time']),
                reason='alfen_transaction_log',
            )
            if completed is not None:
                result['imported'] += 1
                _LOGGER.info("Alfen-Transaktion %s übernommen: %.3f kWh (%s)",
                             tx['transaction_id'], completed['total_kwh'], socket)
            else:
                # Entweder schon bekannt (Stop auf eine nicht mehr aktive
                # Session) oder von der Plausibilitätsprüfung abgelehnt.
                import sqlite3
                conn = sqlite3.connect(session_manager.db_path)
                try:
                    row = conn.execute("SELECT status FROM sessions WHERE id = ?",
                                       (session_id,)).fetchone()
                finally:
                    conn.close()
                status = row[0] if row else None
                if status == 'completed':
                    result['already_known'] += 1
                else:
                    result['not_billable'] += 1
                    _LOGGER.warning("Alfen-Transaktion %s nicht abrechenbar (%s)",
                                    tx.get('transaction_id'), status)
        except Exception as exc:
            result['errors'] += 1
            _LOGGER.warning("Alfen-Transaktion übersprungen (%s): %s",
                            (tx or {}).get('transaction_id', '?'), exc)

    return result
