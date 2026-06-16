import pytest
from fastapi.testclient import TestClient
from app.main import app
import io


@pytest.mark.integration
class TestConversionAPI:
    @pytest.fixture
    def client(self):
        return TestClient(app)
    
    def test_health_check(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data
    
    def test_convert_text_file(self, client):
        content = b"# Test Document\n\nThis is a test."
        files = {"file": ("test.md", io.BytesIO(content), "text/markdown")}
        data = {"image_mode": "base64"}
        
        response = client.post("/api/convert", files=files, data=data)
        assert response.status_code == 200
        result = response.json()
        assert result["status"] == "success"
        assert "markdown" in result
    
    def test_unsupported_format(self, client):
        content = b"binary data"
        files = {"file": ("test.xyz", io.BytesIO(content), "application/octet-stream")}
        
        response = client.post("/api/convert", files=files)
        assert response.status_code == 415
