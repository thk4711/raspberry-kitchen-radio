from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GADGET = ROOT / "buildroot/external/board/radio/rootfs-overlay/usr/sbin/radio-usb-audio-gadget"
BRIDGE = ROOT / "buildroot/external/board/radio/rootfs-overlay/usr/sbin/radio-usb-audio-bridge"
HID = ROOT / "buildroot/external/board/radio/rootfs-overlay/usr/bin/radio-usb-audio-hid"
FRAGMENT = ROOT / "buildroot/external/board/radio/linux-usb-audio.fragment"


def test_kernel_fragment_enables_both_usb_audio_functions():
    text = FRAGMENT.read_text()
    assert "CONFIG_USB_CONFIGFS_F_UAC1=y" in text
    assert "CONFIG_USB_CONFIGFS_F_UAC2=y" in text


def test_kernel_fragment_enables_hid_function():
    text = FRAGMENT.read_text()
    assert "CONFIG_USB_CONFIGFS_F_HID=y" in text


def test_gadget_keeps_uac1_default_and_configures_uac2_rates():
    text = GADGET.read_text()
    assert "mode=uac1" in text
    assert "configured_mode" in text
    assert "FUNCTION=uac2.usb0" in text
    assert '"44100,48000,96000"' in text
    assert "c_ssize" in text


def test_bridge_preserves_uac1_and_uses_uac2_24bit_format():
    text = BRIDGE.read_text()
    assert "format=S16_LE" in text
    assert "S24_3LE/S32_LE scaling path" in text
    assert 'capture_device="plughw:CARD=$GADGET_CARD,DEV=0"' in text
    assert "usb_audio_output.ini" in text
    assert "output_format" in text
    assert 'rate="$rate"' in text


def test_gadget_creates_and_links_composite_hid_function():
    text = GADGET.read_text()
    # HID function is created next to the UAC function and linked into the config.
    assert "HID_FUNCTION=hid.usb0" in text
    assert 'mkdir -p "$GADGET/functions/$HID_FUNCTION"' in text
    assert 'ln -s "$GADGET/functions/$HID_FUNCTION" "$GADGET/configs/$CONFIG/$HID_FUNCTION"' in text
    assert "report_desc" in text
    assert "modprobe usb_f_hid" in text
    # The audio path must remain unaffected when usb_f_hid is unavailable.
    assert "media-key HID function not created" in text


def test_gadget_teardown_removes_hid_function():
    text = GADGET.read_text()
    assert 'rm -f "$GADGET/configs/$CONFIG/$HID_FUNCTION"' in text
    assert 'rmdir "$GADGET/functions/$HID_FUNCTION"' in text


def test_hid_helper_maps_keys_and_is_best_effort():
    text = HID.read_text()
    # Play/Pause bit and a release write, guarded on the device being writable.
    assert "playpause) byte='\\x01'" in text
    assert 'HID_DEVICE="${RADIO_USB_AUDIO_HID_DEVICE:-/dev/hidg0}"' in text
    assert '[ -w "$HID_DEVICE" ] || exit 0' in text
    # Press then release (0x00) so the host sees a single keypress.
    assert "printf '%b' '\\x00'" in text
