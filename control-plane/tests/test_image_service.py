import pytest
from local_ai_control.domain.identity import Role
from local_ai_control.services.generative_media import ImageRequest,ImageService
class Provider:
    def generate(self,request): return ("/private/output.png",)
def test_image_generation_is_owner_only_and_bounded():
    service=ImageService(Provider())
    assert service.submit(Role.OWNER,ImageRequest("cat"))
    with pytest.raises(PermissionError): service.submit(Role.PUBLIC,ImageRequest("cat"))
    with pytest.raises(ValueError): service.submit(Role.OWNER,ImageRequest("cat",2048,2048))
def test_image_edit_preserves_private_ref_boundary():
    request=ImageRequest("remove background",input_refs=("owner-private:abc",))
    assert ImageService(Provider()).submit(Role.OWNER,request)==("/private/output.png",)
