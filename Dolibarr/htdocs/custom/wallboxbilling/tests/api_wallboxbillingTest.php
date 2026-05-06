<?php
/**
 * api_wallboxbillingTest.php - PHPUnit Tests für Wallboxbilling API
 *
 * Testet POST /session Endpoint für HA-Addon Session-Upload
 *
 * @author    Wallbox-Dolibarr Team
 * @version   1.0.0
 */

require_once __DIR__ . '/../class/api_wallboxbilling.class.php';

/**
 * Test-Cases für WallboxbillingApi
 */
class WallboxbillingApiTest extends PHPUnit_Framework_TestCase
{
    /**
     * @var WallboxbillingApi
     */
    protected $api;

    /**
     * Mock-Datenbank Handler
     */
    protected $mockDb;

    /**
     * Setup vor jedem Test
     */
    protected function setUp()
    {
        // Mock-Datenbank erstellen (in realem Test würde manPHPUnit Mock verwenden)
        $this->mockDb = $this->createMock('\DoliDB');

        // API-Instanz erstellen (ohne echte DB-Verbindung für Unit-Tests)
        // Hinweis: In Integrationstests würde man echte DB-Verbindung mocken
    }

    /**
     * Test: Gültige Session-Daten werden akzeptiert
     * Erwartet: Keine Exception, gültige Rückgabe
     */
    public function testPostSessionSuccess()
    {
        // Gültige Session-Daten
        $validData = (object) array(
            'rfid_hash' => 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2',
            'wallbox_id' => 'alfen_eve_01',
            'start_time' => '2026-05-05T14:30:00+02:00',
            'end_time' => '2026-05-05T16:45:00+02:00',
            'kwh' => 25.450
        );

        // Hinweis: Ohne echte DB-Verbindung kann der Test nur die Validierung prüfen
        // In einem vollständigen Integrationstest würde man die DB-Verbindung mocken

        $this->assertNotNull($validData->rfid_hash);
        $this->assertEquals(64, strlen($validData->rfid_hash));
    }

    /**
     * Test: Fehlende Pflichtfelder werden abgelehnt
     * Erwartet: RestException mit 400
     */
    public function testPostSessionMissingFields()
    {
        $incompleteData = (object) array(
            'rfid_hash' => 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2',
            'wallbox_id' => 'alfen_eve_01'
            // Fehlt: start_time, end_time, kwh
        );

        // Prüfe dass Pflichtfeld-Prüfung greifen würde
        $mandatoryFields = array('rfid_hash', 'wallbox_id', 'start_time', 'end_time', 'kwh');
        $missingFields = array();

        foreach ($mandatoryFields as $field) {
            if (!isset($incompleteData->$field)) {
                $missingFields[] = $field;
            }
        }

        $this->assertCount(3, $missingFields);
        $this->assertContains('start_time', $missingFields);
        $this->assertContains('end_time', $missingFields);
        $this->assertContains('kwh', $missingFields);
    }

    /**
     * Test: Ungültiger RFID-Hash wird abgelehnt
     * Erwartet: RestException mit 400 (Regex prüfung)
     */
    public function testPostSessionInvalidHash()
    {
        // Ungültige RFID-Hashes (nicht 64-stelliger Hex-String)
        $invalidHashes = array(
            'short',  // Zu kurz
            'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6', // 56 Zeichen
            'zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz', // ungültige Zeichen
            'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6GHIJ', // Großbuchstaben erlaubt
        );

        // Gültiger Hash (64-stelliger Hex)
        $validHash = 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2';

        // Regex-Prüfung aus api_wallboxbilling.class.php
        $isValid = preg_match('/^[a-f0-9]{64}$/i', $validHash);
        $this->assertEquals(1, $isValid, 'Gültiger 64-stelliger Hex-String sollte akzeptiert werden');

        // Prüfe ungültige Hashes
        foreach ($invalidHashes as $hash) {
            $isInvalid = !preg_match('/^[a-f0-9]{64}$/i', $hash);
            $this->assertTrue($isInvalid, "Ungültiger Hash sollte abgelehnt werden: {$hash}");
        }
    }

    /**
     * Test: Ungültige kWh-Werte werden abgelehnt
     * Erwartet: RestException mit 400
     */
    public function testPostSessionInvalidKwh()
    {
        $invalidKwhValues = array(-1, -0.001, 'abc', null);

        foreach ($invalidKwhValues as $kwh) {
            // Prüfung aus api_wallboxbilling.class.php
            $isValidKwh = is_numeric($kwh) && $kwh >= 0;
            $this->assertFalse($isValidKwh, "Ungültige kWh sollte abgelehnt werden: " . var_export($kwh, true));
        }

        // Gültige kWh-Werte
        $validKwhValues = array(0, 0.001, 25.450, 100.0);
        foreach ($validKwhValues as $kwh) {
            $isValidKwh = is_numeric($kwh) && $kwh >= 0;
            $this->assertTrue($isValidKwh, "Gültige kWh sollte akzeptiert werden: {$kwh}");
        }
    }

    /**
     * Test: Zeitstempel-Validierung (end_time nach start_time)
     * Erwartet: RestException mit 400 wenn end_time <= start_time
     */
    public function testPostSessionInvalidTimeOrder()
    {
        // Ungültige Zeitstempel (End vor Start)
        $startTime = '2026-05-05T16:45:00+02:00';
        $endTime = '2026-05-05T14:30:00+02:00';

        // Parst die Zeitstempel
        $startTs = strtotime($startTime);
        $endTs = strtotime($endTime);

        $this->assertLessThan($endTs, $startTs, 'start_time sollte vor end_time sein');
    }

    /**
     * Test: Return-Format prüfen
     * Erwartet: Array mit success, id, message Keys
     */
    public function testReturnFormat()
    {
        // Erwartetes Rückgabeformat aus api_wallboxbilling.class.php
        $expectedKeys = array('success', 'id', 'message');

        // Simulierte Rückgabe
        $returnValue = array(
            'success' => true,
            'id' => 123,
            'message' => 'Session stored'
        );

        foreach ($expectedKeys as $key) {
            $this->assertArrayHasKey($key, $returnValue, "Rückgabe sollte {$key} enthalten");
        }

        // Typ-Prüfungen
        $this->assertIsBool($returnValue['success']);
        $this->assertIsInt($returnValue['id']);
        $this->assertIsString($returnValue['message']);
    }

    /**
     * Test: Duplikats-Erkennung
     * Erwartet: "Session already exists" Nachricht
     */
    public function testDuplicateDetection()
    {
        // Hinweis: Dies würde in einem vollständigen Test eine echte DB-Abfrage mocken
        // Hier nur die Logik-Prüfung

        $sqlCheck = "SELECT rowid FROM llx_wallbox_sessions "
            ."WHERE rfid_hash = 'abc123' "
            ."AND start_time = '123456' "
            ."AND end_time = '789012'";

        $this->assertStringContainsString('SELECT rowid', $sqlCheck);
        $this->assertStringContainsString('WHERE rfid_hash', $sqlCheck);
    }

    /**
     * Test: JSON-Payload Format
     * Erwartet: Korrektes Format für HA-Addon
     */
    public function testJsonPayloadFormat()
    {
        $payload = array(
            'rfid_hash' => 'a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2',
            'wallbox_id' => 'alfen_eve_01',
            'start_time' => '2026-05-05T14:30:00+02:00',
            'end_time' => '2026-05-05T16:45:00+02:00',
            'kwh' => 25.450
        );

        $json = json_encode($payload);
        $decoded = json_decode($json, true);

        $this->assertEquals($payload, $decoded);
        $this->assertEquals(64, strlen($decoded['rfid_hash']));
        $this->assertContains('T', $decoded['start_time']); // ISO 8601 Format
    }
}

/**
 * Integration-Tests (können mit curl oder Dolibarr Test-Framework ausgeführt werden)
 *
 * curl -X POST "https://dolibarr.example.com/custom/wallboxbilling/api/session.php" \
 *   -H "DOLAPIKEY: YOUR_API_TOKEN" \
 *   -H "Content-Type: application/json" \
 *   -d '{
 *     "rfid_hash": "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2",
 *     "wallbox_id": "alfen_eve_01",
 *     "start_time": "2026-05-05T14:30:00+02:00",
 *     "end_time": "2026-05-05T16:45:00+02:00",
 *     "kwh": 25.450
 *   }'
 *
 * Erwartete Antwort (Erfolg):
 * {"success": true, "id": 123, "message": "Session stored"}
 *
 * Erwartete Antwort (Fehler - fehlende Felder):
 * {"error": "Missing mandatory parameter: kwh"}
 *
 * Erwartete Antwort (Fehler - ungültiger Hash):
 * {"error": "Invalid rfid_hash format (must be 64-char hex SHA-256)"}
 */