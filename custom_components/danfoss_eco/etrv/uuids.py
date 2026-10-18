"""GATT UUIDs of the Danfoss eTRV.

Cross-checked against res/values/strings.xml of the official Danfoss Eco app
(1.6.2). Everything under the 1002xxxx service is XXTEA-encrypted except PIN
and SECRET_KEY.
"""

SERVICE = "10020000-2749-0001-0000-00805f9b042f"

PIN = "10020001-2749-0001-0000-00805f9b042f"
SETTINGS = "10020003-2749-0001-0000-00805f9b042f"
TEMPERATURE = "10020005-2749-0001-0000-00805f9b042f"
NAME = "10020006-2749-0001-0000-00805f9b042f"
CURRENT_TIME = "10020008-2749-0001-0000-00805f9b042f"
ERRORS = "10020009-2749-0001-0000-00805f9b042f"
SECRET_KEY = "1002000b-2749-0001-0000-00805f9b042f"  # readable only in pairing mode
# Mon-Wed (20 bytes) / Thu-Fri (12) / Sat-Sun (12)
SCHEDULE = (
    "1002000d-2749-0001-0000-00805f9b042f",
    "1002000e-2749-0001-0000-00805f9b042f",
    "1002000f-2749-0001-0000-00805f9b042f",
)

BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"

# Standard Device Information Service (0x180A), plaintext strings.
DEVICE_INFORMATION = {
    "firmware_revision": "00002a26-0000-1000-8000-00805f9b34fb",
    "software_revision": "00002a28-0000-1000-8000-00805f9b34fb",
    "hardware_revision": "00002a27-0000-1000-8000-00805f9b34fb",
    "serial_number": "00002a25-0000-1000-8000-00805f9b34fb",
    "model_number": "00002a24-0000-1000-8000-00805f9b34fb",
    "manufacturer": "00002a29-0000-1000-8000-00805f9b34fb",
}
