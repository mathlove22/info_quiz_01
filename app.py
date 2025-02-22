import streamlit as st
import os
import json
import random
import re
import datetime
import time  # ★ 입력 속도 측정을 위해 time 모듈 사용
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# --------------------------------------------------------------
# 커스텀 CSS 및 자바스크립트 (Ctrl+V 완전 차단)
# --------------------------------------------------------------
st.markdown(
    """
    <style>
    .main {
        background-color: #f0f2f6;
    }
    .title {
        font-family: 'Helvetica', sans-serif;
        font-size: 2.8em;
        color: #3366cc;
        text-align: center;
        margin-bottom: 20px;
    }
    .header {
        font-family: 'Helvetica', sans-serif;
        font-size: 1.75em;
        color: #333;
        margin-top: 20px;
        margin-bottom: 10px;
    }
    .subheader {
        font-family: 'Helvetica', sans-serif;
        font-size: 1.25em;
        color: #555;
    }
    .score-card {
        background-color: #ffffff;
        border-radius: 10px;
        padding: 20px;
        margin-top: 20px;
        box-shadow: 0 4px 8px rgba(0,0,0,0.1);
    }
    .criterion-item {
        margin-bottom: 10px;
    }
    </style>
    <script>
      // Ctrl+V 단축키 차단
      document.addEventListener('keydown', function(e) {
          if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'v') {
              e.preventDefault();
          }
      });
      // 모든 textarea 요소에서 붙여넣기 이벤트를 차단
      window.addEventListener('load', function(){
          var textareas = document.getElementsByTagName('textarea');
          for(var i=0; i<textareas.length; i++){
              textareas[i].addEventListener("paste", function(e) {
                  e.preventDefault();
              });
          }
      });
    </script>
    """,
    unsafe_allow_html=True,
)

# --------------------------------------------------------------
# 구글 시트 인증 및 데이터 로드
# --------------------------------------------------------------
# API 접근 범위 (읽기/쓰기 모두 가능)
scope = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

credentials = ServiceAccountCredentials.from_json_keyfile_dict(
    st.secrets["gcp_service_account"], scope
)
gc = gspread.authorize(credentials)

# [general] 섹션에서 스프레드시트 ID 읽기
general_secrets = st.secrets.get("general", {})
questions_sheet_id = general_secrets.get("questions_sheet_id")
criteria_sheet_id = general_secrets.get("criteria_sheet_id")
results_sheet_id  = general_secrets.get("results_sheet_id")  # 기록용 시트 ID (쓰기 권한 필요)

if not questions_sheet_id or not criteria_sheet_id:
    st.error("문제 또는 채점 기준 스프레드시트 ID가 설정되지 않았습니다.")
    st.stop()

# 문제 및 채점 기준 데이터는 한 번만 불러오기
if "questions_data" not in st.session_state:
    questions_sh = gc.open_by_key(questions_sheet_id)
    questions_ws = questions_sh.get_worksheet(0)
    st.session_state.questions_data = questions_ws.get_all_records()

if "criteria_data" not in st.session_state:
    criteria_sh = gc.open_by_key(criteria_sheet_id)
    criteria_ws = criteria_sh.get_worksheet(0)
    criteria = criteria_ws.get_all_records()
    criteria.sort(key=lambda x: float(x["최소비율"]), reverse=True)
    st.session_state.criteria_data = criteria

questions_data = st.session_state.questions_data
criteria_data = st.session_state.criteria_data

# --------------------------------------------------------------
# 문제 목록 표시 (랜덤 6문항 추출)
# --------------------------------------------------------------
if len(questions_data) < 6:
    st.error("문제 데이터가 6문항 미만입니다.")
    st.stop()

if "selected_questions" not in st.session_state:
    st.session_state.selected_questions = random.sample(questions_data, 6)
selected_questions = st.session_state.selected_questions

# --------------------------------------------------------------
# 제출 상태 초기화 (최초 실행 시)
# --------------------------------------------------------------
if "submitted" not in st.session_state:
    st.session_state.submitted = False

# --------------------------------------------------------------
# 입력 속도 측정용 세션 상태
# --------------------------------------------------------------
if "last_time" not in st.session_state:
    st.session_state.last_time = time.time()
if "last_length" not in st.session_state:
    st.session_state.last_length = 0

# --------------------------------------------------------------
# 앱 UI 구성: 제목, 학생 정보 (제출 후 수정 불가)
# --------------------------------------------------------------
st.markdown('<div class="title">논술형 문제 채점 시스템</div>', unsafe_allow_html=True)

with st.sidebar:
    st.markdown('<div class="header">학생 정보</div>', unsafe_allow_html=True)
    submitted_flag = st.session_state.submitted
    student_id = st.text_input("학번", key="student_id", disabled=submitted_flag)
    student_name = st.text_input("이름", key="student_name", disabled=submitted_flag)

# 제출 버튼은 학번/이름이 없거나 이미 제출된 경우 비활성화
submit_disabled = st.session_state.submitted or not (student_id.strip() and student_name.strip())

# --------------------------------------------------------------
# 문제 및 답안 입력 (제출 후 수정 불가)
# --------------------------------------------------------------
st.markdown('<div class="header">문제 및 답안</div>', unsafe_allow_html=True)

answers = {}
for idx, q in enumerate(selected_questions):
    st.markdown(f'<div class="subheader">문제 {idx+1}</div>', unsafe_allow_html=True)
    st.info(q["문제"], icon="✍️")
    ans = st.text_area(
        f"문제 {idx+1} 답안 작성:",
        key=f"answer_{idx}",
        disabled=submitted_flag
    )
    answers[idx] = ans

# --------------------------------------------------------------
# 입력 속도 계산
#   - 모든 답안의 길이 합을 구해, 이전 길이와 비교
# --------------------------------------------------------------
current_time = time.time()
total_answer_length = sum(len(a) for a in answers.values())
time_diff = current_time - st.session_state.last_time
char_diff = total_answer_length - st.session_state.last_length

typing_speed = char_diff / time_diff if time_diff else 0

# 임계값 초과 시 경고
if typing_speed > 300:
    st.warning("입력 속도가 매우 빠릅니다. 붙여넣기 사용이 의심됩니다.")

st.markdown(f"**현재 추정 입력 속도**: {typing_speed:.2f} chars/sec")

# 세션 상태 갱신
st.session_state.last_time = current_time
st.session_state.last_length = total_answer_length

# --------------------------------------------------------------
# Gemini 2.0 LLM을 이용한 채점 함수
# --------------------------------------------------------------
def grade_all_answers_with_gemini(combined_prompt):
    api_key = st.secrets["gcp_service_account"].get("GEMINI_API_KEY")
    if not api_key:
        st.error("GEMINI_API_KEY 환경 변수가 설정되어 있지 않습니다.")
        return None
    genai.configure(api_key=api_key)
    
    generation_config = {
        "temperature": 1,
        "top_p": 0.95,
        "top_k": 40,
        "max_output_tokens": 8192,
        "response_mime_type": "text/plain",
    }
    model = genai.GenerativeModel(
        model_name="gemini-2.0-flash-exp",
        generation_config=generation_config,
    )
    chat_session = model.start_chat(history=[])
    response = chat_session.send_message(combined_prompt)
    
    try:
        json_match = re.search(r'({.*})', response.text, re.DOTALL)
        if json_match:
            pure_json = json_match.group(1)
            result = json.loads(pure_json)
        else:
            st.error("적절한 JSON 형태의 응답을 찾지 못했습니다.")
            result = None
    except Exception as e:
        st.error("응답 파싱 실패: " + str(e))
        result = None

    return result

# --------------------------------------------------------------
# 제출 버튼 처리
# --------------------------------------------------------------
if st.button("제출", disabled=submit_disabled):
    if not (student_id.strip() and student_name.strip()):
        st.error("학번과 이름을 반드시 입력해 주세요.")
    elif any(not ans.strip() for ans in answers.values()):
        st.error("모든 문제에 대해 답안을 작성해 주세요.")
    else:
        # 학생 답안과 모범답안을 합쳐 프롬프트 작성
        combined_prompt = "아래는 각 문제와 모범답안, 학생의 답안입니다:\n\n"
        for idx, q in enumerate(selected_questions):
            combined_prompt += f"문제 {idx+1}:\n"
            combined_prompt += "문제:\n" + q["문제"] + "\n"
            combined_prompt += "모범답안:\n" + q["모범답안"] + "\n"
            combined_prompt += "학생의 답안:\n" + answers[idx] + "\n"
            combined_prompt += "---------------------\n"
        
        # 채점 기준 추가
        criteria_text = "채점 기준은 다음과 같습니다:\n"
        for crit in criteria_data:
            criteria_text += f"최소비율 {crit['최소비율']}%: {crit['설명']} (점수: {crit['점수']})\n"
        combined_prompt += "\n" + criteria_text
        
        # 기대하는 JSON 포맷 안내
        expected_json = (
            "\n위 정보를 바탕으로, 각 문제 별 학생 답안과 모범답안의 유사도를 0부터 100 사이의 백분율로 산출하고, "
            "해당 기준에 따라 점수를 부여하십시오. 최종 결과는 아래 JSON 형식으로 출력해 주세요:\n"
            '{ "문제1": {"score": 0, "유사도": 0.0, "설명": ""}, '
            '"문제2": {"score": 0, "유사도": 0.0, "설명": ""}, '
            '"문제3": {"score": 0, "유사도": 0.0, "설명": ""}, '
            '"문제4": {"score": 0, "유사도": 0.0, "설명": ""}, '
            '"문제5": {"score": 0, "유사도": 0.0, "설명": ""}, '
            '"문제6": {"score": 0, "유사도": 0.0, "설명": ""}, '
            '"총점": 0 }'
        )
        combined_prompt += expected_json

        with st.spinner("채점 중입니다... 잠시만 기다려 주세요!"):
            result = grade_all_answers_with_gemini(combined_prompt)
        
        if result:
            try:
                # 채점 결과 표시
                score_blocks = ""
                total_score = result.get("총점", 0)
                for i in range(6):
                    q_result = result.get(f"문제{i+1}", {})
                    score = q_result.get("score", 0)
                    similarity = q_result.get("유사도", 0.0)
                    explanation = q_result.get("설명", "")
                    score_blocks += (
                        f'<p class="criterion-item"><strong>문제 {i+1}</strong>: '
                        f'{score}점, 유사도: {similarity:.2f}%, {explanation}</p>'
                    )
                
                result_card = f"""
                <div class="score-card">
                    <h2>{student_name} ({student_id})님의 채점 결과</h2>
                    <h3>총점: {total_score}점</h3>
                    <hr>
                    {score_blocks}
                </div>
                """
                st.markdown(result_card, unsafe_allow_html=True)
                
                # 결과 기록용 시트에 추가 기록
                if results_sheet_id:
                    # 붙여넣기 의심 여부
                    suspicious_flag = "Y" if typing_speed > 300 else "N"

                    submission_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    row = [student_id, student_name, submission_time, total_score]
                    
                    # 각 문제별 세부 내용
                    for i in range(6):
                        q = selected_questions[i]
                        q_result = result.get(f"문제{i+1}", {})
                        score = q_result.get("score", 0)
                        remark = q_result.get("설명", "")
                        row.extend([q["문제"], answers.get(i, ""), score, remark])
                    
                    # 입력 속도 & 의심 여부
                    row.extend([f"{typing_speed:.2f}", suspicious_flag])
                    
                    results_sh = gc.open_by_key(results_sheet_id)
                    results_ws = results_sh.get_worksheet(0)
                    results_ws.append_row(row)
                else:
                    st.warning("결과 기록용 스프레드시트 ID(results_sheet_id)가 설정되어 있지 않습니다.")
                
                # 제출 끝 -> 재제출/수정 불가
                st.session_state.submitted = True

            except Exception as e:
                st.error("채점 결과 처리 중 오류 발생: " + str(e))
