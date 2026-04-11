# Streamlit 배포 가이드

## 1) GitHub 업로드
- 이 프로젝트를 GitHub 저장소로 올립니다.
- 최소 필요 파일: `app.py`, `requirements.txt`

## 2) Streamlit Community Cloud 배포
- [share.streamlit.io](https://share.streamlit.io) 접속
- GitHub 저장소 선택
- Main file path를 `app.py`로 지정
- Deploy 클릭

## 3) 주의 사항 (OCR)
- 현재 OCR은 `pytesseract` + 로컬 Tesseract 엔진 기반입니다.
- Streamlit Cloud에서는 시스템 패키지(Tesseract) 설치 제약 때문에 OCR이 동작하지 않을 수 있습니다.
- 클라우드에서 OCR까지 안정적으로 쓰려면 Google Vision/Naver CLOVA 같은 외부 OCR API 방식으로 전환하는 것을 권장합니다.

## 4) 로컬 실행
```bash
pip install -r requirements.txt
streamlit run app.py
```
