import time
import random
import math
import threading
import tkinter as tk
from pynput import keyboard, mouse
from pynput.keyboard import Key, Controller

# ══════════════════════════════════════════════════
# 설정 및 상수
# ══════════════════════════════════════════════════
NUM_LOCATIONS = 2
BATTERY_LIFESPAN = 30.0
NOISE_DEADLINE = 8.0

NOISE_KEYS = ['q', '4', '2', 'e', 'w', 'a', 't', Key.page_down]
TELEPORT_KEY = Key.up
BATTERY_KEY = 'd'

POST_DELAY_TELEPORT = 1.0  # 텔레포트 후딜레이 (초)
POST_DELAY_ACTION   = 0.4  # 배터리/노이즈 후딜레이 (초)

kb_controller = Controller()

# ══════════════════════════════════════════════════
# 내부 게임 환경 시뮬레이터 (규칙 및 점수 관리)
# ══════════════════════════════════════════════════
class GameEnv:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()
        
    def reset(self):
        with self.lock:
            self.locations = [None] * NUM_LOCATIONS # None or battery_name
            self.batteries = {'A': 0, 'B': 0, 'C': 0} # name -> expiry time
            self.battery_queue = ['A', 'B', 'C'] # Next to use
            
            self.current_loc = 0
            self.score = 0.0
            self.risk_score = 0.0
            
            self.last_action_time = 0
            self.post_delay_until = 0
            self.last_noise_time = time.time()
            
            self.action_history = [] # For pattern detection

    def tick(self, now):
        """매 틱마다 시간 경과에 따른 배터리 소멸, 점수, 위험도 계산"""
        with self.lock:
            # 1. 배터리 만료 처리
            for b_name in list(self.batteries.keys()):
                if self.batteries[b_name] > 0 and now >= self.batteries[b_name]:
                    self.batteries[b_name] = 0
                    # 지점 전력 꺼짐
                    for i in range(NUM_LOCATIONS):
                        if self.locations[i] == b_name:
                            self.locations[i] = None
                            
            # 2. 점수 계산 (매 틱 0.1초마다)
            lit_count = sum(1 for loc in self.locations if loc is not None)
            if lit_count == NUM_LOCATIONS:
                self.score += 2.0  # 모두 켜져있으면 고득점 보상
            elif lit_count > 0:
                self.score += 0.5  # 일부만 켜짐
            else:
                self.score -= 1.0  # 모두 꺼짐 (불이익)
                
            # 3. 위험도(Risk) 자연 감소 및 8초 노이즈 룰 위반 체크
            self.risk_score = max(0.0, self.risk_score - 0.2) # 초당 -2점 감소 효과 (0.1초당 0.2)
            
            if now - self.last_noise_time > NOISE_DEADLINE:
                self.risk_score += 5.0 # 8초 규칙 어기면 위험도 폭증
                
    def _check_post_delay(self, now, action_name):
        if now < self.post_delay_until:
            self.score -= 50.0 # 딜레이 중 행동 강행 페널티
            self.risk_score += 15.0 # 봇 의심 증가
            print(f"\n[PENALTY] 딜레이 중 {action_name} 사용! 점수 감점")
            return False
        return True

    def _record_action_risk(self, now, action_type):
        """행동 패턴에 따른 위험도 증가 시뮬레이션"""
        interval = now - self.last_action_time
        self.last_action_time = now
        
        # 기계적인 칼타이밍 방지
        if interval < 0.1:
            self.risk_score += 10.0
            
        self.action_history.append(action_type)
        if len(self.action_history) > 10:
            self.action_history.pop(0)
            
        # 반복 패턴 감지 (예: 이동->설치->이동->설치)
        if len(self.action_history) >= 4:
            seq = "".join(self.action_history[-4:])
            if seq in ["TDTD", "DTDT"]: # T: Teleport, D: Battery
                self.risk_score += 20.0
                
    def do_teleport(self, now):
        with self.lock:
            if not self._check_post_delay(now, "텔레포트"):
                return
            self._record_action_risk(now, "T")
            
            self.current_loc = (self.current_loc + 1) % NUM_LOCATIONS
            self.post_delay_until = now + POST_DELAY_TELEPORT
            
            # 위험도 소폭 증가 (이동 자체의 리스크)
            self.risk_score += 2.0

    def do_battery(self, now):
        with self.lock:
            if not self._check_post_delay(now, "배터리 설치"):
                return
            self._record_action_risk(now, "D")
            
            # 큐에서 다음 배터리 꺼내기
            b_name = self.battery_queue.pop(0)
            self.battery_queue.append(b_name) # 다시 맨 뒤로 (A->B->C 순환)
            
            # 이전 위치에서 제거
            for i in range(NUM_LOCATIONS):
                if self.locations[i] == b_name:
                    self.locations[i] = None
                    
            # 현재 위치에 설치 (30초)
            self.locations[self.current_loc] = b_name
            self.batteries[b_name] = now + BATTERY_LIFESPAN
            self.post_delay_until = now + POST_DELAY_ACTION
            
            # 배터리 설치 리스크
            self.risk_score += 3.0

    def do_noise(self, now):
        with self.lock:
            if not self._check_post_delay(now, "노이즈"):
                return
            self._record_action_risk(now, "N")
            
            self.last_noise_time = now
            self.post_delay_until = now + POST_DELAY_ACTION
            
            # 노이즈는 무작위성이 크므로 위험도 감소에 도움 (또는 아주 적게 증가)
            self.risk_score += 0.5
            
    def get_state(self):
        with self.lock:
            return {
                "loc": self.current_loc,
                "power": [self.locations[i] is not None for i in range(NUM_LOCATIONS)],
                "time_left": [
                    (self.batteries[self.locations[i]] - time.time()) if self.locations[i] else 0 
                    for i in range(NUM_LOCATIONS)
                ],
                "score": self.score,
                "risk": self.risk_score,
                "post_delay_rem": max(0, self.post_delay_until - time.time()),
                "noise_rem": max(0, NOISE_DEADLINE - (time.time() - self.last_noise_time))
            }


# ══════════════════════════════════════════════════
# 인공지능 봇 에이전트 (역산 및 행동 결정)
# ══════════════════════════════════════════════════
class BotAgent:
    def __init__(self, env):
        self.env = env
        self.next_action_time = 0
        
    def step(self, now):
        state = self.env.get_state()
        
        # 1. 후딜레이 중이면 무조건 대기
        if state["post_delay_rem"] > 0:
            return
            
        # 2. 지정된 다음 행동 시간이 안되었으면 대기 (인간적인 멈춤)
        if now < self.next_action_time:
            return
            
        # 3. 위험도(Risk)가 너무 높으면 "휴식" 또는 "노이즈만" 수행하여 회피
        if state["risk"] > 80.0:
            # 생존 우선: 배터리가 꺼지더라도 점수 포기하고 랜덤하게 쉼
            if state["noise_rem"] < 2.0:
                self.execute_action("NOISE", now)
            else:
                # 가만히 쉬기
                self.next_action_time = now + random.uniform(1.0, 3.0)
            return

        # 4. 배터리 유지 전략 수립
        # 어느 지점이 전력이 가장 부족한가?
        target_loc = None
        min_time = 999
        for i in range(NUM_LOCATIONS):
            t = state["time_left"][i]
            if t < min_time:
                min_time = t
                target_loc = i
                
        # 배터리가 5초 미만 남았거나 아예 없으면 최우선으로 충전하러 감
        if min_time < 5.0:
            if state["loc"] == target_loc:
                self.execute_action("BATTERY", now)
            else:
                self.execute_action("TELEPORT", now)
            return
            
        # 5. 8초 노이즈 마감 압박 해결
        if state["noise_rem"] < random.uniform(2.0, 4.0):
            self.execute_action("NOISE", now)
            return
            
        # 6. 여유 상황: 봇 감지를 피하기 위해 의도적으로 노이즈를 섞거나 랜덤 딜레이
        action_choice = random.choices(
            ["NOISE", "WAIT", "PREEMPTIVE_TELEPORT"],
            weights=[40, 50, 10], k=1
        )[0]
        
        if action_choice == "NOISE":
            self.execute_action("NOISE", now)
        elif action_choice == "PREEMPTIVE_TELEPORT":
            # 전략적 텔레포트 (미리 이동해두기)
            self.execute_action("TELEPORT", now)
        else:
            self.next_action_time = now + random.uniform(0.5, 2.5)

    def execute_action(self, action, now):
        global simulating
        simulating = True
        
        # 실제 물리적 키 누름 & 환경 업데이트
        if action == "TELEPORT":
            hold = random.uniform(0.08, 0.15)
            kb_controller.press(TELEPORT_KEY)
            time.sleep(hold)
            kb_controller.release(TELEPORT_KEY)
            self.env.do_teleport(now)
            
        elif action == "BATTERY":
            hold = random.uniform(0.04, 0.1)
            kb_controller.press(BATTERY_KEY)
            time.sleep(hold)
            kb_controller.release(BATTERY_KEY)
            self.env.do_battery(now)
            
        elif action == "NOISE":
            key = random.choice(NOISE_KEYS)
            hold = random.uniform(0.04, 0.1)
            if isinstance(key, Key):
                kb_controller.press(key)
                time.sleep(hold)
                kb_controller.release(key)
            else:
                kb_controller.press(key)
                time.sleep(hold)
                kb_controller.release(key)
            self.env.do_noise(now)
            
        simulating = False
        # 행동 완료 후 인간적인 판단 딜레이 (0.1 ~ 0.4초) 추가
        self.next_action_time = time.time() + random.uniform(0.1, 0.4)


# ══════════════════════════════════════════════════
# 전역 변수 및 제어 제어
# ══════════════════════════════════════════════════
env = GameEnv()
bot = BotAgent(env)

active = False
simulating = False
pause_timer = None
HUMAN_PAUSE_DURATION = 5.0

def resume_bot():
    global active
    print("\n[SYSTEM] 5초 경과. 봇 자동 재개. 배터리 초기화됨.")
    env.reset()
    active = True
    update_dot()

def on_human_input():
    global active, pause_timer
    if simulating: return
    
    if active:
        print("\n[SYSTEM] 사용자 입력 감지! 봇 일시정지 (타이머 리셋).")
        active = False
        update_dot()
        
    # 기존 타이머 취소 및 새 타이머 시작
    if pause_timer is not None:
        pause_timer.cancel()
    pause_timer = threading.Timer(HUMAN_PAUSE_DURATION, resume_bot)
    pause_timer.daemon = True
    pause_timer.start()

# 리스너 콜백
def on_press(key):
    if key == Key.caps_lock and not simulating:
        global active, pause_timer
        active = not active
        if active:
            if pause_timer: pause_timer.cancel()
            env.reset()
            print("\n[SYSTEM] Caps Lock ON - 봇 가동 시작")
        else:
            print("\n[SYSTEM] Caps Lock OFF - 봇 정지")
        update_dot()
        return
        
    if key == Key.pause:
        print("\n[SYSTEM] 종료합니다.")
        import os
        os._exit(0)
        
    on_human_input()

def on_click(x, y, button, pressed):
    if pressed: on_human_input()
def on_move(x, y):
    pass # 마우스 이동은 너무 민감할 수 있으나 요청에 따라 포함하려면 주석 해제. 하지만 일반적으로 클릭/키보드 감지가 안정적임.
    # on_human_input() 

# ══════════════════════════════════════════════════
# UI 및 메인 루프
# ══════════════════════════════════════════════════
root = tk.Tk()
root.overrideredirect(True)
root.attributes("-topmost", True)
root.attributes("-transparentcolor", "black")
root.geometry("20x20+{}+{}".format(root.winfo_screenwidth() - 30, 10))
root.configure(bg='black')

canvas = tk.Canvas(root, width=20, height=20, bg='black', highlightthickness=0)
canvas.pack()
dot = canvas.create_oval(2, 2, 18, 18, fill="red", outline="")

def update_dot():
    color = "#00FF00" if active else "#FF0000"
    canvas.itemconfig(dot, fill=color)

def print_status_loop():
    while True:
        if active:
            state = env.get_state()
            p1 = f"{state['time_left'][0]:04.1f}s" if state['power'][0] else " OFF "
            p2 = f"{state['time_left'][1]:04.1f}s" if state['power'][1] else " OFF "
            loc_str = "▶ L1  L2" if state['loc'] == 0 else "  L1 ▶L2"
            
            risk_color = "🔴" if state['risk'] > 80 else "🟡" if state['risk'] > 50 else "🟢"
            
            # 한 줄로 콘솔에 계속 덮어씌우며 출력
            print(f"점수: {state['score']:08.1f} | 위험도: {risk_color} {state['risk']:05.1f} | 위치: {loc_str} | 전력: [{p1}] [{p2}] | 후딜: {state['post_delay_rem']:03.1f}s | 노이즈여유: {state['noise_rem']:03.1f}s   ", end='\r')
        time.sleep(0.1)

def main_loop():
    # 초당 10번 업데이트 (0.1초 틱)
    while True:
        now = time.time()
        if active:
            env.tick(now)
            bot.step(now)
        time.sleep(0.1)

# 쓰레드 시작
threading.Thread(target=main_loop, daemon=True).start()
threading.Thread(target=print_status_loop, daemon=True).start()

kb_listener = keyboard.Listener(on_press=on_press)
mouse_listener = mouse.Listener(on_click=on_click) # 마우스 클릭 감지

kb_listener.start()
mouse_listener.start()

root.mainloop()
