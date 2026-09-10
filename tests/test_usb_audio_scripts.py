from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GADGET = ROOT / "buildroot/external/board/radio/rootfs-overlay/usr/sbin/radio-usb-audio-gadget"
BRIDGE = ROOT / "buildroot/external/board/radio/rootfs-overlay/usr/sbin/radio-usb-audio-bridge"
FRAGMENT = ROOT / "buildroot/external/board/radio/linux-usb-audio.fragment"


def test_kernel_fragment_enables_both_usb_audio_functions():
    text = FRAGMENT.read_text()
    assert "CONFIG_USB_CONFIGFS_F_UAC1=y" in text
    assert "CONFIG_USB_CONFIGFS_F_UAC2=y" in text


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
