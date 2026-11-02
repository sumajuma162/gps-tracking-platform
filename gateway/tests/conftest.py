import pytest

from gpsgw.normalization.io_mapper import IoMappingProfile
from gpsgw.protocols.base import DeviceIdentity, SessionContext, Transport
from gpsgw.protocols.teltonika.decoder import TeltonikaDecoder


@pytest.fixture
def decoder() -> TeltonikaDecoder:
    return TeltonikaDecoder()


@pytest.fixture
def ctx() -> SessionContext:
    return SessionContext(Transport.TCP, "test:1", identity=DeviceIdentity("356307042441013"))


@pytest.fixture
def fmc920_profile() -> IoMappingProfile:
    return IoMappingProfile.load_builtin("teltonika_fmc920")
