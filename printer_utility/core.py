from __future__ import annotations

import contextlib
import re
import struct
from dataclasses import dataclass, field
from typing import Iterable


EPSON_VENDOR_ID = 0x04B8
ET_2820_PRODUCT_ID = 0x1186
ET_2820_READ_KEY = 0x364A
ET_2820_WRITE_KEY = b"Nbsjcbzb"

CMD_ENTER_D4 = b"\x00\x00\x00\x1b\x01@EJL 1284.4\n@EJL\n@EJL\n"
CMD_ENTER_D4_REPLY = b"\x00\x00\x00\x08\x01\x00\xc5\x00"
D4_HEADER = struct.Struct(">BBHBB")

STATUS_TEXT = {
    0x00: "Error",
    0x01: "Self Printing",
    0x02: "Busy",
    0x03: "Waiting",
    0x04: "Idle",
    0x05: "Paused",
    0x07: "Cleaning",
    0x08: "Factory shipment",
    0x0A: "Shutdown",
    0x0F: "Nozzle Check",
    0x11: "Charging",
}

ERROR_TEXT = {
    0x00: "Fatal error",
    0x01: "Other interface selected",
    0x02: "Cover open",
    0x04: "Paper jam",
    0x05: "Ink out",
    0x06: "Paper out",
    0x0C: "Paper path/type/size error",
    0x10: "Waste ink pad counter overflow",
    0x11: "Wait return from tear-off position",
    0x12: "Double feed",
    0x1A: "Cartridge cover open",
    0x1C: "Cutter fatal error",
    0x1D: "Cutter jam",
    0x22: "Maintenance cartridge missing",
    0x25: "Rear cover open",
    0x29: "CD-R tray out",
    0x2A: "Memory card loading error",
    0x2B: "Tray cover open",
    0x2C: "Ink cartridge overflow",
    0x33: "Initial filling impossible",
    0x36: "Maintenance cartridge cover open",
    0x37: "Scanner or front cover open",
    0x41: "Maintenance request",
    0x47: "Printing disabled",
    0x4A: "Maintenance box near end",
    0x4B: "Driver mismatch",
}

WARNING_TEXT = {
    0x10: "Ink low",
    0x11: "Ink low",
    0x12: "Ink low",
    0x13: "Ink low",
    0x14: "Ink low",
    0x44: "Black print mode",
    0x51: "Cleaning disabled: cyan",
    0x52: "Cleaning disabled: magenta",
    0x53: "Cleaning disabled: yellow",
    0x54: "Cleaning disabled: black",
}

INK_COLOR_TEXT = {
    0x00: "Black",
    0x01: "Cyan",
    0x02: "Magenta",
    0x03: "Yellow",
    0x04: "Light Cyan",
    0x05: "Light Magenta",
    0x06: "Dark Yellow",
    0x07: "Grey",
    0x08: "Light Black",
    0x09: "Red",
    0x0A: "Blue",
    0x0B: "Gloss Optimizer",
    0x0C: "Light Grey",
    0x0D: "Orange",
}

ET_2820_COUNTER_ADDRESSES = [
    0x01C,
    0x034,
    0x035,
    0x036,
    0x037,
    0x0FF,
    0x02F,
    0x030,
    0x031,
    0x032,
    0x033,
    0x0FC,
    0x0FD,
    0x0FE,
]

ET_2820_RESET_PREVIEW = {
    0x01C: 0x00,
    0x034: 0x00,
    0x035: 0x00,
    0x036: 0x5E,
    0x037: 0x5E,
    0x0FF: 0x5E,
    0x02F: 0x00,
    0x030: 0x00,
    0x031: 0x00,
    0x032: 0x00,
    0x033: 0x00,
    0x0FC: 0x00,
    0x0FD: 0x00,
    0x0FE: 0x00,
}


class PrinterError(RuntimeError):
    pass


@dataclass(frozen=True)
class UsbInterfaceInfo:
    number: int
    alternate: int
    class_code: int
    subclass_code: int
    protocol_code: int
    label: str = ""
    bulk_in: int | None = None
    bulk_out: int | None = None

    @property
    def is_service_candidate(self) -> bool:
        label = self.label.lower()
        return self.number == 2 or "utility" in label


@dataclass(frozen=True)
class UsbPrinterInfo:
    vendor_id: int
    product_id: int
    manufacturer: str
    product: str
    serial: str
    bus: int | None
    address: int | None
    interfaces: tuple[UsbInterfaceInfo, ...]

    @property
    def display_name(self) -> str:
        return f"{self.product or 'EPSON printer'} ({self.vendor_id:04X}:{self.product_id:04X})"

    @property
    def service_interface(self) -> UsbInterfaceInfo | None:
        for interface in self.interfaces:
            if interface.is_service_candidate and interface.bulk_in and interface.bulk_out:
                return interface
        for interface in self.interfaces:
            if interface.bulk_in and interface.bulk_out:
                return interface
        return None


@dataclass(frozen=True)
class StatusSummary:
    status_code: int | None = None
    status_text: str = "Unknown"
    error_code: int | None = None
    error_text: str = ""
    ready: bool = False
    warnings: tuple[str, ...] = ()
    ink_levels: tuple[tuple[str, int], ...] = ()
    paper_counts: tuple[int, ...] = ()
    maintenance_boxes: tuple[str, ...] = ()
    serial_info: str = ""
    raw_hex: str = ""
    fields: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ProbeResult:
    printer: UsbPrinterInfo
    device_id: dict[str, object]
    status: StatusSummary
    counters: dict[int, int | None]


@dataclass(frozen=True)
class ResetResult:
    printer: UsbPrinterInfo
    device_id: dict[str, object]
    before: dict[int, int | None]
    target: dict[int, int]
    after: dict[int, int | None]
    changed: tuple[int, ...]
    verified: bool


def _load_usb():
    try:
        import usb.core
        import usb.util
        import usb.backend.libusb1
        import libusb_package
    except Exception as exc:
        raise PrinterError(
            "Missing USB dependencies. Install requirements.txt first."
        ) from exc

    backend = usb.backend.libusb1.get_backend(find_library=libusb_package.find_library)
    if backend is None:
        raise PrinterError("libusb backend was not found.")
    return usb, backend


def _get_string(usb, device, index: int | None) -> str:
    if not index:
        return ""
    try:
        return usb.util.get_string(device, index) or ""
    except Exception:
        return ""


def scan_epson_usb_printers() -> list[UsbPrinterInfo]:
    usb, backend = _load_usb()
    printers: list[UsbPrinterInfo] = []
    for device in usb.core.find(find_all=True, idVendor=EPSON_VENDOR_ID, backend=backend):
        interfaces: list[UsbInterfaceInfo] = []
        manufacturer = _get_string(usb, device, device.iManufacturer)
        product = _get_string(usb, device, device.iProduct)
        serial = _get_string(usb, device, device.iSerialNumber)

        try:
            configs = list(device)
        except Exception:
            configs = []

        for config in configs:
            for interface in config:
                label = _get_string(usb, device, interface.iInterface)
                bulk_in = None
                bulk_out = None
                for endpoint in interface:
                    endpoint_type = usb.util.endpoint_type(endpoint.bmAttributes)
                    direction = usb.util.endpoint_direction(endpoint.bEndpointAddress)
                    if endpoint_type != usb.util.ENDPOINT_TYPE_BULK:
                        continue
                    if direction == usb.util.ENDPOINT_IN:
                        bulk_in = endpoint.bEndpointAddress
                    else:
                        bulk_out = endpoint.bEndpointAddress
                interfaces.append(
                    UsbInterfaceInfo(
                        number=int(interface.bInterfaceNumber),
                        alternate=int(interface.bAlternateSetting),
                        class_code=int(interface.bInterfaceClass),
                        subclass_code=int(interface.bInterfaceSubClass),
                        protocol_code=int(interface.bInterfaceProtocol),
                        label=label,
                        bulk_in=bulk_in,
                        bulk_out=bulk_out,
                    )
                )

        printers.append(
            UsbPrinterInfo(
                vendor_id=int(device.idVendor),
                product_id=int(device.idProduct),
                manufacturer=manufacturer,
                product=product,
                serial=serial,
                bus=getattr(device, "bus", None),
                address=getattr(device, "address", None),
                interfaces=tuple(interfaces),
            )
        )
    return printers


def parse_ieee1284_id(payload: bytes | str) -> dict[str, object]:
    if isinstance(payload, bytes):
        text = payload.decode("ascii", errors="replace")
    else:
        text = payload
    if "@EJL ID" in text:
        text = text.split("@EJL ID", 1)[1].strip()
    result: dict[str, object] = {}
    for part in text.split(";"):
        key, sep, value = part.partition(":")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if key == "CMD":
            result[key] = tuple(item for item in value.split(",") if item)
        else:
            result[key] = value
    if "MANUFACTURER" in result and "MFG" not in result:
        result["MFG"] = result["MANUFACTURER"]
    if "MODEL" in result and "MDL" not in result:
        result["MDL"] = result["MODEL"]
    return result


def parse_status(data: bytes) -> StatusSummary:
    header = b"@BDC ST2\r\n"
    start = data.find(header)
    if start < 0:
        raise PrinterError("Printer returned an unrecognized status packet.")
    pos = start + len(header)
    if len(data) < pos + 2:
        raise PrinterError("Printer status packet is truncated.")
    payload_len = int.from_bytes(data[pos : pos + 2], "little")
    payload = data[pos + 2 : pos + 2 + payload_len]
    if len(payload) != payload_len:
        raise PrinterError("Printer status packet length is invalid.")

    fields: dict[str, object] = {}
    warnings: list[str] = []
    ink_levels: list[tuple[str, int]] = []
    paper_counts: tuple[int, ...] = ()
    maintenance_boxes: list[str] = []
    status_code: int | None = None
    error_code: int | None = None
    serial_info = ""

    buf = payload
    while buf:
        if len(buf) < 2:
            break
        field_type, length = buf[0], buf[1]
        item = buf[2 : 2 + length]
        buf = buf[2 + length :]
        if len(item) != length:
            break

        if field_type == 0x01 and item:
            status_code = item[0]
            fields["status"] = STATUS_TEXT.get(status_code, f"Unknown {status_code}")
        elif field_type == 0x02 and item:
            error_code = item[0]
            fields["error"] = ERROR_TEXT.get(error_code, f"Unknown {error_code}")
        elif field_type == 0x04:
            warnings.extend(WARNING_TEXT.get(code, f"Unknown {code}") for code in item)
        elif field_type == 0x0F and item:
            stride = max(1, item[0])
            offset = 1
            while offset + 2 < len(item):
                color_code = item[offset + 1]
                level = item[offset + 2]
                ink_levels.append((INK_COLOR_TEXT.get(color_code, f"0x{color_code:02X}"), level))
                offset += stride
        elif field_type == 0x36 and len(item) == 20:
            paper_counts = tuple(
                int.from_bytes(item[index : index + 4], "little", signed=True)
                for index in range(0, 20, 4)
            )
        elif field_type == 0x37 and item:
            width = item[0]
            if width in (1, 2):
                for index in range(1, len(item), width):
                    state = item[index]
                    if state == 0:
                        text = "not full"
                    elif state == 1:
                        text = "near full"
                    elif state == 2:
                        text = "full"
                    else:
                        text = f"unknown {state}"
                    maintenance_boxes.append(text)
        elif field_type == 0x40:
            serial_info = item.decode("ascii", errors="replace")
        else:
            fields[f"0x{field_type:02X}"] = item.hex(" ")

    status_text = STATUS_TEXT.get(status_code, "Unknown") if status_code is not None else "Unknown"
    error_text = ERROR_TEXT.get(error_code, "") if error_code is not None else ""
    ready = status_code in (0x03, 0x04)
    return StatusSummary(
        status_code=status_code,
        status_text=status_text,
        error_code=error_code,
        error_text=error_text,
        ready=ready,
        warnings=tuple(warnings),
        ink_levels=tuple(ink_levels),
        paper_counts=paper_counts,
        maintenance_boxes=tuple(maintenance_boxes),
        serial_info=serial_info,
        raw_hex=data.hex(" "),
        fields=fields,
    )


class EpsonD4Client:
    def __init__(
        self,
        *,
        vendor_id: int = EPSON_VENDOR_ID,
        product_id: int = ET_2820_PRODUCT_ID,
        interface_number: int = 2,
        timeout_ms: int = 3000,
    ) -> None:
        self.vendor_id = vendor_id
        self.product_id = product_id
        self.interface_number = interface_number
        self.timeout_ms = timeout_ms
        self._rx_buffer = b""
        self._usb = None
        self._backend = None
        self._device = None
        self._endpoint_in = None
        self._endpoint_out = None
        self._claimed = False
        self._revision = 0x20
        self._channel_credit: dict[tuple[int, int], int] = {}

    def __enter__(self) -> "EpsonD4Client":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def open(self) -> None:
        usb, backend = _load_usb()
        self._usb = usb
        self._backend = backend
        self._device = usb.core.find(
            idVendor=self.vendor_id,
            idProduct=self.product_id,
            backend=backend,
        )
        if self._device is None:
            raise PrinterError("Epson USB printer was not found.")

        try:
            config = self._device.get_active_configuration()
        except Exception:
            configs = list(self._device)
            if not configs:
                raise PrinterError("Printer has no readable USB configuration.")
            config = configs[0]

        interface = usb.util.find_descriptor(config, bInterfaceNumber=self.interface_number)
        if interface is None:
            raise PrinterError(f"USB interface {self.interface_number} was not found.")
        endpoint_in = usb.util.find_descriptor(
            interface,
            custom_match=lambda endpoint: (
                usb.util.endpoint_type(endpoint.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
                and usb.util.endpoint_direction(endpoint.bEndpointAddress) == usb.util.ENDPOINT_IN
            ),
        )
        endpoint_out = usb.util.find_descriptor(
            interface,
            custom_match=lambda endpoint: (
                usb.util.endpoint_type(endpoint.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK
                and usb.util.endpoint_direction(endpoint.bEndpointAddress) == usb.util.ENDPOINT_OUT
            ),
        )
        if endpoint_in is None or endpoint_out is None:
            raise PrinterError(f"USB interface {self.interface_number} has no bulk I/O endpoints.")

        try:
            usb.util.claim_interface(self._device, self.interface_number)
            self._claimed = True
        except Exception as exc:
            raise PrinterError(
                f"Could not claim Epson utility interface {self.interface_number}: {exc}"
            ) from exc
        self._endpoint_in = endpoint_in
        self._endpoint_out = endpoint_out

    def close(self) -> None:
        if self._usb is not None and self._device is not None and self._claimed:
            with contextlib.suppress(Exception):
                self._usb.util.release_interface(self._device, self.interface_number)
        if self._usb is not None and self._device is not None:
            with contextlib.suppress(Exception):
                self._usb.util.dispose_resources(self._device)
        self._claimed = False
        self._device = None
        self._endpoint_in = None
        self._endpoint_out = None
        self._rx_buffer = b""
        self._channel_credit.clear()

    def read_device_id(self) -> dict[str, object]:
        response = self._control_exchange(self._encode_control("di", b"\x01"))[0]
        return parse_ieee1284_id(response)

    def read_status(self) -> StatusSummary:
        response = self._control_exchange(self._encode_control("st", b"\x01"))[0]
        return parse_status(response)

    def read_eeprom(self, addresses: Iterable[int]) -> dict[int, int | None]:
        messages = [self._encode_read_eeprom(address) for address in addresses]
        responses = self._control_exchange(*messages)
        values: dict[int, int | None] = {}
        for address, response in zip(addresses, responses):
            values[int(address)] = self._parse_eeprom_response(int(address), response)
        return values

    def write_eeprom(self, values: dict[int, int]) -> dict[int, bool]:
        messages = [
            self._encode_write_eeprom(address, value)
            for address, value in values.items()
        ]
        responses = self._control_exchange(*messages)
        return {
            address: b":OK;" in response
            for address, response in zip(values.keys(), responses)
        }

    def _control_exchange(self, *messages: bytes) -> list[bytes]:
        if self._device is None:
            raise PrinterError("USB client is not open.")
        self._enter_d4_mode()
        self._transaction_init()
        self._open_channel(2, 2)
        try:
            replies = [self._channel_exchange((2, 2), message) for message in messages]
        finally:
            with contextlib.suppress(Exception):
                self._close_channel(2, 2)
            with contextlib.suppress(Exception):
                self._transaction_exit()
        return replies

    def _enter_d4_mode(self) -> None:
        self._write(CMD_ENTER_D4)
        received = b""
        for _ in range(5):
            chunk = self._read_raw(silent_timeout=True)
            if not chunk:
                break
            received += chunk
            if CMD_ENTER_D4_REPLY in received:
                return

    def _transaction_init(self) -> None:
        self._send_packet((0, 0), b"\x00" + bytes([self._revision]))
        payload = self._read_for_channel((0, 0), expected_codes={0x80})
        if len(payload) < 3:
            raise PrinterError(f"D4 init failed: {payload.hex(' ')}")
        result, revision = payload[1], payload[2]
        if result == 0x02 and revision in (0x10, 0x20) and revision != self._revision:
            self._revision = revision
            self._transaction_init()
            return
        if result != 0x00:
            raise PrinterError(f"D4 init failed: {payload.hex(' ')}")

    def _transaction_exit(self) -> None:
        self._send_packet((0, 0), b"\x08")
        with contextlib.suppress(Exception):
            self._read_for_channel((0, 0), expected_codes={0x88})

    def _open_channel(self, sid_p: int, sid_s: int) -> None:
        if self._revision == 0x10:
            payload = b"\x01" + struct.pack(">BBHHHH", sid_p, sid_s, 0x0100, 0x0100, 0, 0)
        else:
            payload = b"\x01" + struct.pack(">BBHHH", sid_p, sid_s, 0x0100, 0x0100, 0)
        self._send_packet((0, 0), payload)
        reply = self._read_for_channel((0, 0), expected_codes={0x81})
        if len(reply) < 4 or reply[1] != 0x00:
            raise PrinterError(f"Could not open Epson control channel: {reply.hex(' ')}")
        self._channel_credit[(sid_p, sid_s)] = 0

    def _close_channel(self, sid_p: int, sid_s: int) -> None:
        if self._revision == 0x10:
            payload = b"\x02" + struct.pack(">BBB", sid_p, sid_s, 0)
        else:
            payload = b"\x02" + struct.pack(">BB", sid_p, sid_s)
        self._send_packet((0, 0), payload)
        self._read_for_channel((0, 0), expected_codes={0x82})
        self._channel_credit.pop((sid_p, sid_s), None)

    def _channel_exchange(self, channel: tuple[int, int], payload: bytes) -> bytes:
        self._ensure_channel_credit(channel)
        self._send_packet(channel, payload)
        self._channel_credit[channel] = max(0, self._channel_credit.get(channel, 0) - 1)
        return self._read_for_channel(channel)

    def _ensure_channel_credit(self, channel: tuple[int, int]) -> None:
        if self._channel_credit.get(channel, 0) > 0:
            return
        if self._revision == 0x10:
            payload = b"\x04" + struct.pack(">BBHH", channel[0], channel[1], 0x0080, 0xFFFF)
        else:
            payload = b"\x04" + struct.pack(">BBH", channel[0], channel[1], 0)
        self._send_packet((0, 0), payload)
        reply = self._read_for_channel((0, 0), expected_codes={0x84})
        if len(reply) < 5 or reply[1] != 0x00:
            raise PrinterError(f"Could not obtain D4 channel credit: {reply.hex(' ')}")
        add_credit = int.from_bytes(reply[-2:], "big")
        if add_credit <= 0:
            raise PrinterError("Printer granted no D4 channel credit.")
        self._channel_credit[channel] = self._channel_credit.get(channel, 0) + add_credit

    def _send_packet(self, channel: tuple[int, int], payload: bytes, credit: int = 1) -> None:
        packet = D4_HEADER.pack(channel[0], channel[1], D4_HEADER.size + len(payload), credit, 0) + payload
        self._write(packet)

    def _read_for_channel(
        self,
        channel: tuple[int, int],
        *,
        expected_codes: set[int] | None = None,
    ) -> bytes:
        for _ in range(12):
            header, payload = self._read_packet()
            if (header[0], header[1]) != channel:
                continue
            if expected_codes is not None and (not payload or payload[0] not in expected_codes):
                continue
            return payload
        raise PrinterError(f"No response from D4 channel {channel}.")

    def _read_packet(self) -> tuple[tuple[int, int, int, int, int], bytes]:
        while True:
            if len(self._rx_buffer) >= D4_HEADER.size:
                header = D4_HEADER.unpack(self._rx_buffer[: D4_HEADER.size])
                packet_len = header[2]
                if len(self._rx_buffer) >= packet_len:
                    payload = self._rx_buffer[D4_HEADER.size : packet_len]
                    self._rx_buffer = self._rx_buffer[packet_len:]
                    return header, payload
            self._rx_buffer += self._read_raw()

    def _read_raw(self, *, silent_timeout: bool = False) -> bytes:
        if self._endpoint_in is None:
            raise PrinterError("USB input endpoint is not open.")
        try:
            data = self._endpoint_in.read(512, timeout=self.timeout_ms)
        except Exception as exc:
            name = exc.__class__.__name__.lower()
            if silent_timeout and "timeout" in name:
                return b""
            raise PrinterError(f"USB read failed: {exc}") from exc
        return bytes(data)

    def _write(self, data: bytes) -> None:
        if self._endpoint_out is None:
            raise PrinterError("USB output endpoint is not open.")
        try:
            written = self._endpoint_out.write(data, timeout=self.timeout_ms)
        except Exception as exc:
            raise PrinterError(f"USB write failed: {exc}") from exc
        if int(written) <= 0:
            raise PrinterError("USB write returned no bytes written.")

    @staticmethod
    def _encode_control(command: str, payload: bytes = b"") -> bytes:
        return command.encode("ascii") + struct.pack("<H", len(payload)) + payload

    @staticmethod
    def _encode_read_eeprom(address: int) -> bytes:
        command = ord("A")
        factory_prefix = struct.pack(
            "<HBBB",
            ET_2820_READ_KEY,
            command,
            (~command) & 0xFF,
            ((command >> 1) & 0x7F) | ((command << 7) & 0x80),
        )
        payload = factory_prefix + struct.pack("<H", address)
        return b"||" + struct.pack("<H", len(payload)) + payload

    @staticmethod
    def _encode_write_eeprom(address: int, value: int) -> bytes:
        command = ord("B")
        factory_prefix = struct.pack(
            "<HBBB",
            ET_2820_READ_KEY,
            command,
            (~command) & 0xFF,
            ((command >> 1) & 0x7F) | ((command << 7) & 0x80),
        )
        payload = factory_prefix + struct.pack("<HB", address, value) + ET_2820_WRITE_KEY
        return b"||" + struct.pack("<H", len(payload)) + payload

    @staticmethod
    def _parse_eeprom_response(address: int, response: bytes) -> int | None:
        match = re.search(rb"EE:([0-9a-fA-F]{6});", response)
        if not match:
            return None
        raw = bytes.fromhex(match.group(1).decode("ascii"))
        if len(raw) != 3:
            return None
        reported_address, value = struct.unpack(">HB", raw)
        if reported_address != address:
            return None
        return value


def probe_printer(printer: UsbPrinterInfo) -> ProbeResult:
    service_interface = printer.service_interface
    if service_interface is None:
        raise PrinterError("No usable Epson utility interface was found.")
    with EpsonD4Client(
        vendor_id=printer.vendor_id,
        product_id=printer.product_id,
        interface_number=service_interface.number,
    ) as client:
        device_id = client.read_device_id()
        status = client.read_status()
        counters = client.read_eeprom(ET_2820_COUNTER_ADDRESSES)
    return ProbeResult(printer=printer, device_id=device_id, status=status, counters=counters)


def build_reset_preview(counters: dict[int, int | None]) -> list[tuple[int, int | None, int]]:
    return [
        (address, counters.get(address), reset_value)
        for address, reset_value in ET_2820_RESET_PREVIEW.items()
    ]


def reset_ink_pad_counter(printer: UsbPrinterInfo) -> ResetResult:
    service_interface = printer.service_interface
    if service_interface is None:
        raise PrinterError("No usable Epson utility interface was found.")

    with EpsonD4Client(
        vendor_id=printer.vendor_id,
        product_id=printer.product_id,
        interface_number=service_interface.number,
    ) as client:
        device_id = client.read_device_id()
        model = str(device_id.get("MDL", printer.product or ""))
        if not model.startswith("ET-2820"):
            raise PrinterError(f"Refusing reset for unsupported model: {model}")

        before = client.read_eeprom(ET_2820_RESET_PREVIEW.keys())
        unreadable = [address for address, value in before.items() if value is None]
        if unreadable:
            formatted = ", ".join(f"0x{address:03X}" for address in unreadable)
            raise PrinterError(f"Refusing reset because these bytes could not be read: {formatted}")

        target = dict(ET_2820_RESET_PREVIEW)
        changed = tuple(
            address
            for address, target_value in target.items()
            if before.get(address) != target_value
        )
        if changed:
            write_result = client.write_eeprom({address: target[address] for address in target})
            failed = [address for address, ok in write_result.items() if not ok]
            if failed:
                formatted = ", ".join(f"0x{address:03X}" for address in failed)
                raise PrinterError(f"Reset write failed at: {formatted}")

        after = client.read_eeprom(target.keys())

    verified = all(after.get(address) == value for address, value in target.items())
    if not verified:
        failed = [
            f"0x{address:03X}"
            for address, value in target.items()
            if after.get(address) != value
        ]
        raise PrinterError(f"Reset verification failed at: {', '.join(failed)}")

    return ResetResult(
        printer=printer,
        device_id=device_id,
        before=before,
        target=target,
        after=after,
        changed=changed,
        verified=verified,
    )
