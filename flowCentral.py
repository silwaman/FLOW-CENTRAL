
# ==========================
# IMPORTS
# ==========================
import os
import re
import time
import pickle
from io import StringIO
from typing import Optional

import pandas as pd
import streamlit as st

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# ==========================
# CONFIGURAÇÕES GERAIS
# ==========================
TARGET_URL = f"https://autoflow-cascade-na.amazon.com/GRU5/dashboard/"
COOKIE_FILE = "./autoflow_cookies.pkl"
PAGE_LOAD_TIMEOUT = 30
WAIT = 15


# ==========================
# DRIVER
# ==========================
def configurar_navegador():
    chrome_options = Options()
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    # chrome_options.add_argument("--headless=new")  # sem janela visível
    chrome_options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options,
    )
    return driver

# ==========================
# COOKIES (persistência de sessão)
# ==========================
def load_cookies_from_disk():
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, "rb") as f:
                return pickle.load(f)
        except Exception:
            return None
    return None

def save_cookies_to_disk(driver):
    try:
        cookies = driver.get_cookies()
        with open(COOKIE_FILE, "wb") as f:
            pickle.dump(cookies, f)
    except Exception:
        pass

def _safe_add_cookie(driver, c: dict) -> bool:
    # remove chaves None para evitar erro do Selenium
    allowed = {}
    for k in ("name", "value", "domain", "path", "secure", "httpOnly", "expiry", "sameSite"):
        if k in c and c[k] is not None:
            allowed[k] = c[k]
    try:
        driver.add_cookie(allowed)
        return True
    except Exception:
        return False

def apply_cookies(driver, cookies, url=TARGET_URL):
    """
    Para setar cookies no Chrome, é obrigatório:
      1) abrir o domínio primeiro
      2) adicionar cookie por cookie com domain/path corretos
    """
    if not cookies:
        return
    driver.get(url)  # abre o domínio
    # alguns sites redirecionam; garantir que o domínio de cada cookie exista
    for c in cookies:
        try:
            # Selenium exige, no mínimo, name e value
            # Se o cookie gravado tiver campos a mais, mantemos; se faltar domain/path, Selenium assume
            driver.add_cookie({
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": c.get("domain", None),
                "path": c.get("path", "/"),
                "secure": c.get("secure", False),
                "httpOnly": c.get("httpOnly", False),
                "expiry": c.get("expiry", None),
                "sameSite": c.get("sameSite", None),
            })
        except Exception:
            # ignora cookies incompatíveis com o subdomínio atual
            continue
    driver.refresh()

# ==========================
# LOGIN 
# ==========================
def is_present(driver, by, selector, timeout: int = 3) -> bool:
    try:
        WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, selector)))
        return True
    except Exception:
        return False


def is_clickable(driver, by, selector, timeout: int = WAIT):
    try:
        return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, selector)))
    except Exception:
        return None


def fill(driver, by, selector, value, timeout: int = WAIT):
    elem = is_clickable(driver, by, selector, timeout=timeout)
    if not elem:
        raise RuntimeError(f"Elemento não clicável: {selector}")
    elem.clear()
    elem.send_keys(value)


def is_login_page(driver) -> bool:
    return is_present(driver, By.ID, "user_name", timeout=3)


def is_otp_page(driver) -> bool:
    return is_present(driver, By.ID, "otp-field", timeout=3)


def wait_dashboard_loaded(driver) -> bool:
    try:
        WebDriverWait(driver, WAIT).until_not(EC.presence_of_element_located((By.ID, "user_name")))
    except Exception:
        pass
    # Considera carregado se não há login nem OTP
    return (not is_login_page(driver)) and (not is_otp_page(driver))

# ==========================
# AUTENTICAÇÃO MIDWAY 
# ==========================
def autenticar_midway(driver, username: str, pin: str, otp: str = None):
    """
    Faz o login via Midway usando Selenium.
    Agora só usa o PIN passado pelo Streamlit.
    """
    try:
        # preenche username
        user_field = driver.find_element(By.ID, "user_name")
        user_field.send_keys(username)
        # driver.find_element(By.ID, "continue").click()

        # preenche PIN
        pin_field = driver.find_element(By.ID, "password")
        pin_field.send_keys(pin)
        driver.find_element(By.ID, "signInSubmit").click()

        # se pedir OTP
        if otp:
            otp_field = driver.find_element(By.ID, "auth-mfa-otpcode")
            otp_field.send_keys(otp)
            driver.find_element(By.ID, "otp-submit-btn").click()

        return True
    except Exception as e:
        raise RuntimeError(f"Erro no login Midway: {e}")


# ==========================
# LOGIN PRINCIPAL 
# ==========================
def perform_login(driver, username: str, pin: str, otp: str = None):
    driver.get(TARGET_URL)

    # Tela de username/pin
    try:
        WebDriverWait(driver, WAIT).until(
            EC.presence_of_element_located((By.ID, "user_name"))
        )
        driver.find_element(By.ID, "user_name").send_keys(username)
        driver.find_element(By.ID, "password").send_keys(pin)
        driver.find_element(By.ID, "verify_btn").click()
    except Exception:
        pass  # se já estiver logado, ignora

    # Tela de OTP
    if otp:
        try:
            WebDriverWait(driver, 3).until(
                EC.presence_of_element_located((By.ID, "otp-field"))
            )
            driver.find_element(By.ID, "otp-field").send_keys(otp)
            driver.find_element(By.ID, "otp-submit-btn").click()
        except Exception:
            pass

    # Espera carregar dashboard
    try:
        WebDriverWait(driver, WAIT).until_not(
            EC.presence_of_element_located((By.ID, "user_name"))
        )
    except Exception:
        pass

    # Salva cookies
    try:
        with open(COOKIE_FILE, "wb") as f:
            pickle.dump(driver.get_cookies(), f)
    except Exception:
        pass

    return True
# ===========================================================================DECLARAÇOES =============================================================================================================================

fcs_tw_MS = ["GRU9","GIG1"]
fcs_tw_MZ = ["GRU5"]
fcs_autoflow_MZ = ["GRU5"]
fcs_autoflow_MS = ["GRU9","GIG1"]
fcs_permitidos = [
            "BSB1", "CNF1", "FOR2", "GIG1", "GRU8", "POA1", "REC1", "REC3",
            "GIG2", "XCV9", "GRU5", "GRU9"
        ]

fc = st.sidebar.selectbox("Selecione o FC", fcs_permitidos) # caixa de selecao FC 

# ============================
# 🚀 PARAMETRO TRB default, pri, expr 
# ============================
class TRB:
    def __init__(self):
        self.fc_permitidos = [
            "BSB1", "CNF1", "FOR2", "GIG1", "GRU8", "POA1", "REC1", "REC3",
            "GIG2", "XCV9", "GRU5", "GRU9"
        ]
        self.nao_agregados = [
            "CNF1", "FOR2", "GIG1", "POA1", "REC1", "REC3",
            "XCV9", "GRU9","BSB1", "GRU8"
        ]
        self.agregados = ["GRU5"]

        self.faixas_expedite = {
            'BSB1': {'faixa': '175-180', 'horas': 1},
            'CNF1': {'faixa': '195-200', 'horas': 1},
            'FOR2': {'faixa': '185-190', 'horas': 1},
            'GIG1': {'faixa': '195-200', 'horas': 1.5},
            'GRU5': {'faixa': '185-190', 'horas': 1.5},
            'GRU8': {'faixa': '195-200', 'horas': 1},
            'GRU9': {'faixa': '195-200', 'horas': 1.5},
            'POA1': {'faixa': '87-90',  'horas': 1.5},
            'REC1': {'faixa': '87-90',  'horas': 1},
            'REC3': {'faixa': '195-200','horas': 1.5},
            'XCV9': {'faixa': '195-200','horas': 1}
        }

        self.faixas_priority = {
            'BSB1': {'faixa': '90-95',  'horas': 1.75},
            'CNF1': {'faixa': '90-95',  'horas': 3},
            'FOR2': {'faixa': '90-95',  'horas': 2},
            'GIG1': {'faixa': '90-95',  'horas': 2},
            'GRU5': {'faixa': '90-95',  'horas': 2},
            'GRU8': {'faixa': '90-95',  'horas': 2},
            'GRU9': {'faixa': '90-95',  'horas': 3.5},
            'POA1': {'faixa': '90-95',  'horas': 2},
            'REC1': {'faixa': '90-95',  'horas': 2},
            'REC3': {'faixa': '90-95',  'horas': 3},
            'XCV9': {'faixa': '90-95',  'horas': 2}
        }

        self.faixas_default = {
            'BSB1': {'faixa': '90-95',  'horas': 1.75},
            'CNF1': {'faixa': '90-95',  'horas': 3},
            'FOR2': {'faixa': '90-95',  'horas': 2},
            'GIG1': {'faixa': '90-95',  'horas': 2},
            'GIG2': {'faixa': '87-90',  'horas': 4},
            'GRU5': {'faixa': '90-95',  'horas': 2.25},
            'GRU8': {'faixa': '90-95',  'horas': 2},
            'GRU9': {'faixa': '90-95',  'horas': 3.5},
            'POA1': {'faixa': '90-95',  'horas': 2},
            'REC1': {'faixa': '87-90',  'horas': 2},
            'REC3': {'faixa': '90-95',  'horas': 3},
            'XCV9': {'faixa': '90-95',  'horas': 3}
        }

    def dentro_sla(self, cpt_str, horas_sla):
        try:
            hora_cpt = datetime.strptime(cpt_str.strip(), "%m/%d %H:%M")
            hora_cpt = hora_cpt.replace(year=datetime.now().year, tzinfo=ZoneInfo("America/Sao_Paulo"))
            agora = datetime.now(ZoneInfo("America/Sao_Paulo"))
            inicio_janela = hora_cpt - timedelta(hours=horas_sla)
            return agora < inicio_janela
        except Exception:
            return False

# ============================
# 🚀 TABELA MIN/MAX  WIP 
# ============================
wip_tempos = {
    'GRU5': {'minima': 1.8, 'maxima': 2.2},
    'CNF1': {'minima': 1.5, 'maxima': 2.2},
    'GIG1': {'minima': 1.8, 'maxima': 2.2},
    'FOR2': {'minima': 1.5, 'maxima': 1.8},
    'GRU8': {'minima': 1.0, 'maxima': 1.2},
    'POA1': {'minima': 1.5, 'maxima': 2.0},
    'REC1': {'minima': 1.5, 'maxima': 2.5},
    'REC3': {'minima': 1.8, 'maxima': 2.2},
    'BSB1': {'minima': 1.5, 'maxima': 1.8},
    'GRU9': {'minima': 1.8, 'maxima': 2.2},
    'XCV9': {'minima': 1.5, 'maxima': 1.8}
}


def puxar_wip(navegador, fc):
    url = (
        f'https://rodeo-iad.amazon.com/{fc}/ExSD?yAxis=WORK_POOL&zAxis=NONE&shipmentTypes=ALL&'
        'exSDRange.quickRange=NEXT_3_DAYS&exSDRange.dailyStart=00%3A00&exSDRange.dailyEnd=00%3A00&'
        'giftOption=ALL&fulfillmentServiceClass=ALL&fracs=NON_FRACS&isEulerExSDMiss=ALL&isEulerPromiseMiss=ALL&'
        'isEulerUpgraded=ALL&isReactiveTransfer=ALL&workPool=PredictedCharge&workPool=PlannedShipment&_workPool=on&'
        'workPool=ReadyToPick&workPool=ReadyToPickHardCapped&workPool=ReadyToPickUnconstrained&'
        'workPool=PickingNotYetPicked&workPool=PickingNotYetPickedPrioritized&workPool=PickingNotYetPickedNotPrioritized&'
        'workPool=PickingNotYetPickedHardCapped&workPool=CrossdockNotYetPicked&_workPool=on&workPool=PickingPicked&'
        'workPool=PickingPickedInProgress&workPool=PickingPickedInTransit&workPool=PickingPickedRouting&'
        'workPool=PickingPickedAtDestination&workPool=Inducted&workPool=RebinBuffered&workPool=Sorted&workPool=GiftWrap&'
        'workPool=Packing&workPool=Scanned&workPool=ProblemSolving&workPool=ProcessPartial&workPool=SoftwareException&'
        'workPool=Crossdock&workPool=PreSort&workPool=TransshipSorted&workPool=Palletized&workPool=PalletizedStaged&_workPool=on&'
        'workPool=ManifestPending&workPool=ManifestPendingVerification&workPool=Manifested&workPool=Slammed&'
        'workPool=ReceivedBySorter&workPool=InterceptProblemSolve&workPool=STaRSSlammed&workPool=STaRSReceivedBySorter&'
        'workPool=STaRSDiverted&workPool=STaRSStacked&workPool=STaRSStaged&workPool=STaRSLoaded&workPool=Diverted&'
        'workPool=Stacked&workPool=Staged&workPool=Loaded&workPool=TransshipManifested&_workPool=on&processPath=&'
        'minPickPriority=MIN_PRIORITY&shipMethod=&shipOption=&sortCode=&fnSku='
    )
    navegador.get(url)
    try:
                wip_element = WebDriverWait(navegador, 15).until(
                    EC.presence_of_element_located(
                        (By.XPATH, "//th[normalize-space()='WorkInProgress Subtotal']/following-sibling::td[1]")
                    )
                )
                valor_texto = wip_element.text.strip().replace(",", "")
                return int(valor_texto) if valor_texto.replace(".", "").isdigit() else None
    except Exception as e:
                print(f"❌ Erro ao puxar WIP: {e}")
                return None


def puxar_lagrange(navegador, fc):
    try:
        navegador.get(f"https://throughput-iad.iad.proxy.amazon.com/{fc}/lagrange/")
        time.sleep(5)
        lagrange_element = navegador.find_element(By.XPATH, '//*[@id="OUTBOUNDdefaultThroughputs0"]')
        return int(lagrange_element.text)
    except Exception:
        return 0


def puxar_override(navegador, fc):
    try:
        navegador.get(f"https://throughput-iad.iad.proxy.amazon.com/{fc}/lagrange/")
        override_element = WebDriverWait(navegador, 5).until(
            EC.presence_of_element_located((By.XPATH, '/html/body/div[2]/div[2]/div/div[1]/div/table/td[2]/div/table/tbody[1]/tr[1]/td[1]/input'))
        )
        value = override_element.get_attribute('value')
        return int(value) if value else 0
    except Exception:
        return 0


def validar_wip(fc, wip, lagrange, override):
    if fc not in wip_tempos:
        return "⚠️ FC não encontrado na tabela de tempos."
     # Conversão para float:
    wip = int(wip)
    lagrange = int(lagrange)
    override = int(override)

    tempos = wip_tempos[fc]
    def_min = lagrange * tempos['minima']
    def_max = lagrange * tempos['maxima']
    ov_min = override * tempos['minima']
    ov_max = override * tempos['maxima']

    status = []
    if lagrange > 0:
        if def_min <= wip <= def_max:
            status.append("✅ WIP dentro do intervalo DEFAULT")
        elif wip < def_min:
            perc = ((wip/def_min) * 100 ) - 100
            status.append(f"⬇️ WIP {perc:.1f}% abaixo do mínimo DEFAULT ")
        else:
            perc = ((wip / def_max) * 100 ) - 100
            status.append(f"⬆️ WIP está {perc:.1f}% acima do máximo DEFAULT ")
    else:
        status.append("ℹ️  Valor do Lagrange zerado ")

    if override > 0:
        if ov_min <= wip <= ov_max:
            status.append("✅ WIP dentro do intervalo OVERRIDE")
        elif wip < ov_min:
            perc = ((wip / ov_min) * 100 ) - 100
            status.append(f"⬇️ WIP está {perc:.1f}% abaixo do mínimo OVERRIDE ")
        else:
            perc = ((wip / ov_max) * 100 ) - 100
            status.append(f"⬆️ WIP está {perc:.1f}% acima do máximo OVERRIDE ")
    else:
        status.append("ℹ️ Sem valor de OVERRIDE definido")

    return "\n".join(status)

# ==============================================================
#          PUXAR O TRB PELO CORA
# ==============================================================

def extrair_tabelas(navegador, xpath):
    try:
        container = navegador.find_element(By.XPATH, xpath)
        tabelas_html = container.find_elements(By.TAG_NAME, "table")
        dfs = [pd.read_html(StringIO(t.get_attribute("outerHTML")))[0] for t in tabelas_html if t.get_attribute("outerHTML").strip()]
        return pd.concat(dfs, ignore_index=True) if dfs else None
    except Exception:
        return None


def puxar_trb(navegador, fc, trb: TRB):
    if fc in trb.agregados:
        url = f"https://outboundflow-iad.amazon.com/{fc}/cora?durationOfRiskWindowInDays=7&tableSelector=exsd&fulfillmentProgram=AMAZON_FULFILLMENT"
        navegador.get(url)
        time.sleep(8)
        tabela_html = navegador.find_element(By.TAG_NAME, "table").get_attribute("outerHTML")
        df_agregado = pd.read_html(StringIO(tabela_html))[0]
        return df_agregado, None
    else:
        url = f"https://outboundflow-iad.amazon.com/{fc}/cora?durationOfRiskWindowInDays=7&tableSelector=ppf&fulfillmentProgram=AMAZON_FULFILLMENT"
        navegador.get(url)
        time.sleep(8)
        df_singles = extrair_tabelas(navegador, '//*[@id="cptRiskViewRow"]/div/div[4]/div[2]/div[3]')
        df_multis  = extrair_tabelas(navegador, '//*[@id="cptRiskViewRow"]/div/div[4]/div[2]/div[4]')
        return df_singles, df_multis


def check_status(valor, faixa):
    min_val, max_val = map(float, faixa.split("-"))
    if valor >= max_val:
        return "ATIVO"
    elif valor <= min_val:
        return "INATIVO"
    return "ATENÇÃO"


def verificar_todos(fc, df, trb, tipo):
    linha_utilizacao = df.iloc[9]
    valores = (
        linha_utilizacao.iloc[2:]
        .astype(str)
        .str.replace("%", "")
        .str.strip()
        .replace({"∞": "NaN"})
        .astype(float)
    )

    resultados = []
    for i, valor in enumerate(valores):
        if pd.isna(valor):
            continue

        horario_raw = str(df.iloc[0, i + 2]).strip()

        try:
            ano_atual = datetime.now().year
            horario_com_ano = horario_raw[:5] + f"/{ano_atual}" + horario_raw[5:]
            horario_dt = datetime.strptime(horario_com_ano, "%m/%d/%Y %H:%M")
            horario_dt = horario_dt.replace(tzinfo=ZoneInfo("America/Sao_Paulo"))
            horario_fmt = horario_dt.strftime("%d/%m/%Y %H:%M")
        except Exception:
            horario_fmt = horario_raw

        default_info = trb.faixas_default.get(fc, {"faixa": "0-0", "horas": 0})
        pri_info     = trb.faixas_priority.get(fc, {"faixa": "0-0", "horas": 0})
        expr_info    = trb.faixas_expedite.get(fc, {"faixa": "0-0", "horas": 0})

        dentro_default = trb.dentro_sla(horario_raw, default_info["horas"])    
        dentro_pri     = trb.dentro_sla(horario_raw, pri_info["horas"])        
        dentro_expr    = trb.dentro_sla(horario_raw, expr_info["horas"])       

        status_default = check_status(valor, default_info["faixa"]) if dentro_default else "FORA_SLA"
        status_pri     = check_status(valor, pri_info["faixa"])     if dentro_pri     else "FORA_SLA"
        status_expr    = check_status(valor, expr_info["faixa"])    if dentro_expr    else "FORA_SLA"

        faixas_ativas = [x for x, s in zip(
            ["DEFAULT", "PRI", "EXPR"],
            [status_default, status_pri, status_expr]
        ) if s == "ATIVO"]

        if faixas_ativas:
            msg = f"❌ TRB ATIVO | CPT: {horario_fmt} | Faixa(s): {' /  '.join(faixas_ativas)} | Valor: {valor}%"
        elif any(s == "ATENÇÃO" for s in [status_default, status_pri, status_expr]) and \
                "FORA_SLA" not in [status_default, status_pri, status_expr]:
            msg = f"⚠️ ATENÇÃO | CPT: {horario_fmt} | Valor: {valor}% | Faixa Default: {default_info['faixa']}"
        else:
            continue

        resultados.append([fc, horario_fmt, valor, msg, tipo])

    return resultados

#==============================================================
#              PROCESSAMENTO PPR
# =============================================================

def validar_processamento(processamento, lagrange, override):
    mensagens = []

    def checar_valor(valor_param, nome_param):
        if valor_param <= 0:
            mensagens.append(f"ℹ️ Sem valor válido para {nome_param}.")
            return

        diferenca = processamento - valor_param
        perc = (abs(diferenca) / valor_param) * 100

        if abs(diferenca) <= 0.05 * valor_param:
            mensagens.append(f"✅ Processamento dentro do intervalo aceitável para {nome_param} (±5%).")
        else:
            if diferenca < 0:
                mensagens.append(
                    f"❌ Processamento está {perc:.1f}% abaixo do {nome_param}.\n"
                    
                )
            else:
                mensagens.append(
                    f"❌ Processamento está {perc:.1f}% acima do {nome_param}.\n"
                    
                )

    checar_valor(lagrange, "Lagrange")
    checar_valor(override, "Override")

    return "\n".join(mensagens)


def limpar_numero(texto):
    numero_limpo = re.sub(r'\D', '', texto)
    return int(numero_limpo) if numero_limpo else 0


def arredondar_para_15_minutos(dt):
    minuto = dt.minute
    arredondado = (minuto // 15) * 15
    if arredondado == 60:
        dt += timedelta(hours=1)
        arredondado = 0
    return dt.replace(minute=arredondado, second=0, microsecond=0)


def puxar_processamento(navegador, fc):
    agora = datetime.now()
    fim = arredondar_para_15_minutos(agora)
    inicio = fim - timedelta(hours=1)

    data_str = fim.strftime('%Y/%m/%d')
    start_hour = inicio.hour
    start_minute = str(inicio.minute)
    end_hour = fim.hour
    end_minute = str(fim.minute)

    url = (
        "https://fclm-portal.amazon.com/reports/processPathRollup?reportFormat=HTML"
        f"&warehouseId={fc}&maxIntradayDays=1&spanType=Intraday&startDateIntraday={data_str}"
        f"&startHourIntraday={start_hour}&startMinuteIntraday={start_minute}&endDateIntraday={data_str}"
        f"&endHourIntraday={end_hour}&endMinuteIntraday={end_minute}&_adjustPlanHours=on&_hideEmptyLineItems=on"
        f"&_rememberViewForWarehouse=on&employmentType=AllEmployees"
    )
    navegador.get(url)

    wait = WebDriverWait(navegador, 20)
    try:
                    wait.until(EC.presence_of_element_located((By.ID, "startHourIntraday")))

                    # ajustando a data
                    navegador.find_element(By.ID, "startDateIntraday").clear()
                    navegador.find_element(By.ID, "startDateIntraday").send_keys(data_str)
                    navegador.find_element(By.ID, "endDateIntraday").clear()
                    navegador.find_element(By.ID, "endDateIntraday").send_keys(data_str)


                    # ajustando os horarios 
                    Select(navegador.find_element(By.ID, "startHourIntraday")).select_by_value(str(start_hour))
                    Select(navegador.find_element(By.ID, "startMinuteIntraday")).select_by_value(start_minute)
                    Select(navegador.find_element(By.ID, "endHourIntraday")).select_by_value(str(end_hour))
                    Select(navegador.find_element(By.ID, "endMinuteIntraday")).select_by_value(end_minute)

                    # Clica fora para remover tooltip que bloqueia o botão
                    ActionChains(navegador).move_by_offset(0, 0).click().perform()
                    time.sleep(1)

                    submit_button = wait.until(EC.element_to_be_clickable((By.XPATH, '//a[contains(@class, "cp-submit") and contains(text(), "HTML")]')))
                    submit_button.click()
    except Exception as e:
        print("Erro no submit:", e)
        return None

    time.sleep(5)
    try: # calculando a media de pick e pack mult + single
        pick = navegador.find_element(By.XPATH, '//*[@id="ppr.detail.outbound.pick.pick.total"]/td[3]/div')
        pack_mult = navegador.find_element(By.XPATH, '//*[@id="ppr.detail.outbound.pack.packMultis.total"]/td[3]/div')
        pack_single = navegador.find_element(By.XPATH, '//*[@id="ppr.detail.outbound.pack.packSingle.total"]/td[3]/div')

        pick = limpar_numero(pick.text)
        pack_mult = limpar_numero(pack_mult.text)
        pack_single = limpar_numero(pack_single.text)

        pack = pack_mult + pack_single
        processamento = (pick + pack) / 2
        return {"Pick": pick, "Pack": pack, "Processamento": round(processamento, 2)}
    except Exception as e:
        print("Erro ao extrair processamento:", e)
        return None

# =====================================================================================
#  RETORNAR VALOR DO TW SORTATION 
#======================================================================================

def buffer_sortation(driver, fc):
    navegador = driver
    navegador.get(f'https://flow-sortation-na.amazon.com/{fc}/#/buffer/current-status')
    time.sleep(5)
    html = navegador.page_source
    tabelas = pd.read_html(StringIO(html))
    primeira_tabela = tabelas[0]
    return primeira_tabela



# =======================================================================
# puxando buffers de rebin MZ do autoflow
# ======================================================================

if fc in fcs_autoflow_MZ:
    def buffers_MZ(navegador, fc):
        # GRU5
            try:

                url = f'https://autoflow-cascade-na.amazon.com/{fc}/dashboard/'
                navegador.get(url)
                time.sleep(5)
                
                MZ = navegador.find_element(By.XPATH,'//*[@id="main-body-throughput"]/div[3]/div[1]/div[3]/div[3]/div[1]/div/div[1]/div[1]/a')
                MZ_atual = navegador.find_element(By.XPATH,'//*[@id="main-body-throughput"]/div[3]/div[1]/div[3]/div[3]/div[1]/div/div[1]/div[4]/h4').text
                MZ_min = navegador.find_element(By.XPATH,'//*[@id="main-body-throughput"]/div[3]/div[1]/div[3]/div[3]/div[1]/div/div[1]/div[5]/h4').text    
                MZ_max = navegador.find_element(By.XPATH,'//*[@id="main-body-throughput"]/div[3]/div[1]/div[3]/div[3]/div[1]/div/div[1]/div[6]/h4').text

                return {
                    "MZ": MZ,
                    "MZ_atual": MZ_atual,
                    "MZ_min": MZ_min,
                    "MZ_max": MZ_max
                }
                
            except Exception as e:
                print(f"Erro ao extrair buffer: {e}")
                return None
            
        
# =======================================================================
# puxando buffers de rebin MS do autoflow
# ======================================================================

if fc in fcs_autoflow_MS  and fc == "GRU9":
     
    def buffers_MS(navegador, fc):
        
            try:
                url = f'https://autoflow-cascade-na.amazon.com/{fc}/dashboard/'
                navegador.get(url)
                time.sleep(5)
                
                MS = navegador.find_element(By.XPATH,'//*[@id="main-body-throughput"]/div[3]/div[1]/div[3]/div[2]/div[1]/div/div[1]/div[1]/a')
                MS_atual = navegador.find_element(By.XPATH,'//*[@id="main-body-throughput"]/div[3]/div[1]/div[3]/div[2]/div[1]/div/div[1]/div[4]/h4').text
                MS_min = navegador.find_element(By.XPATH,'//*[@id="main-body-throughput"]/div[3]/div[1]/div[3]/div[2]/div[1]/div/div[1]/div[5]/h4').text    
                MS_max = navegador.find_element(By.XPATH,'//*[@id="main-body-throughput"]/div[3]/div[1]/div[3]/div[2]/div[1]/div/div[1]/div[6]/h4').text

                return {
                    "MS": MS,
                    "MS_atual": MS_atual,
                    "MS_min": MS_min,
                    "MS_max": MS_max
                }
                
            except Exception as e:
                print(f"Erro ao extrair buffer: {e}")
                return None
            

elif fc in fcs_autoflow_MS  and fc == "GIG1":     
    def buffers_MS(navegador, fc):
        
            try:
                url = f'https://autoflow-cascade-na.amazon.com/{fc}/dashboard/'
                navegador.get(url)
                time.sleep(5)
                
                MS = navegador.find_element(By.XPATH,'//*[@id="main-body-throughput"]/div[3]/div[1]/div[3]/div[4]/div[1]/div/div[1]/div[1]/a')
                MS_atual = navegador.find_element(By.XPATH,'//*[@id="main-body-throughput"]/div[3]/div[1]/div[3]/div[4]/div[1]/div/div[1]/div[4]/h4').text
                MS_min = navegador.find_element(By.XPATH,'//*[@id="main-body-throughput"]/div[3]/div[1]/div[3]/div[4]/div[1]/div/div[1]/div[5]/h4').text    
                MS_max = navegador.find_element(By.XPATH,'//*[@id="main-body-throughput"]/div[3]/div[1]/div[3]/div[4]/div[1]/div/div[1]/div[6]/h4').text

                return {
                    "MS": MS,
                    "MS_atual": MS_atual,
                    "MS_min": MS_min,
                    "MS_max": MS_max
                }
                
            except Exception as e:
                print(f"Erro ao extrair buffer: {e}")
                return None


import streamlit as st
import pandas as pd

# ============= Sidebar Login =============
st.sidebar.title("🔐 Credenciais de Acesso")
username = st.sidebar.text_input("👤 Username")
pin = st.sidebar.text_input("🔑 PIN", type="password")
otp = st.sidebar.text_input("📲 OTP (caso necessário)", type="password")

# ============= Ação ao Clicar no Botão Entrar =============
if st.sidebar.button("➡️ Entrar"):
    if not username or not pin:
        st.error("⚠️ Por favor, preencha Username e PIN para continuar.")
    else:
        driver = configurar_navegador()
        try:
            perform_login(driver, username, pin, otp or None)
            st.success("✅ Login realizado com sucesso!")
            st.divider()

            # ======================
            # 🔍 Seção: WIP e Lagrange
            # ======================
            st.subheader("📊 Status Operacional - WIP, Default e Override")
            wip = puxar_wip(driver, fc)
            lagrange = puxar_lagrange(driver, fc)
            override = puxar_override(driver, fc)

            col1, col2, col3 = st.columns(3)
            col1.metric("WIP", wip)
            col2.metric("Default", lagrange)
            col3.metric("Override", override)

            status_wip = validar_wip(fc, wip, lagrange, override)
            st.info(f"🔎 **Status WIP:** {status_wip}")
            st.divider()

            # ============================
            # 🧮 Seção: Processamento pelo PPR
            # ============================
            st.subheader("🧮 Processamento pelo PPR")
            processamento_data = puxar_processamento(driver, fc)

            if processamento_data:
                st.json(processamento_data)
                process_status = validar_processamento(
                    processamento_data["Processamento"], lagrange, override
                )
                st.info(f"📌 **Status do Processamento:** {process_status}")
            else:
                st.warning("❌ Não foi possível extrair os dados de processamento FCLM.")
            st.divider()

            # ==========================
            # 📦 Buffer TW (Sortation)
            # ==========================
            st.subheader("📦 Buffer TW - Sortation")
            try:
                if fc in fcs_autoflow_MZ:
                    df = buffer_sortation(driver, fc)
                    valor = df.loc[df["Destination"] == "pkMULTIZONE", "Buffers Utilization"].values[0]
                    st.success(f"🔄 Utilização atual do buffer TW (Multizone): **{valor}**")
                elif fc in fcs_autoflow_MS:
                    df = buffer_sortation(driver, fc)
                    valor = df.loc[df["Destination"] == "pkMULTISMALL", "Buffers Utilization"].values[0]
                    st.success(f"🔄 Utilização atual do buffer TW (Multismall): **{valor}**")
            except:
                st.warning("⚠️ Não foi possível extrair o buffer TW.")
            st.divider()

            # ===========================
            # 📊 Buffer Rebin Autoflow
            # ===========================
            st.subheader("📊 Buffer Rebin - Autoflow")

            def exibir_buffer(tipo, buffers):
                atual = float(buffers[f"{tipo}_atual"].replace('%', '').strip())
                minimo = float(buffers[f"{tipo}_min"].replace('%', '').strip())
                maximo = float(buffers[f"{tipo}_max"].replace('%', '').strip())

                st.write(f"### Buffer {tipo}")
                st.write(f"- Atual: **{atual}**")
                st.write(f"- Mínimo: **{minimo}**\n- Máximo: **{maximo}**")

                if atual < minimo:
                    st.warning(f"⬇️ Está **{100 - int((atual / minimo) * 100)}% abaixo do mínimo!**")
                elif atual > maximo:
                    st.warning(f"⬆️ Está **{int(((atual / maximo) * 100) - 100)}% acima do máximo!**")
                else:
                    st.success("✅ Dentro dos limites aceitáveis.")

            try:
                if fc in fcs_autoflow_MZ:
                    buffers = buffers_MZ(driver, fc)
                    if buffers:
                        exibir_buffer("MZ", buffers)
                    else:
                        st.warning("❗Não foi possível extrair dados do buffer MZ.")

                if fc in fcs_autoflow_MS:
                    buffers = buffers_MS(driver, fc)
                    if buffers:
                        exibir_buffer("MS", buffers)
                    else:
                        st.warning("❗Não foi possível extrair dados do buffer MS.")
            except:
                st.error("Erro ao carregar dados dos buffers.")
            st.divider()

            try:
                    trb = TRB()
                    df_singles, df_multis = puxar_trb(driver, fc, trb)
                    resultados = []

                    if df_singles is not None:
                        resultados += verificar_todos(fc, df_singles, trb, "Singles")
                    if df_multis is not None:
                        resultados += verificar_todos(fc, df_multis, trb, "Multis")

                    if resultados:
                        df_result = pd.DataFrame(resultados, columns=["FC", "CPT", "Valor (%)", "Mensagem", "Tipo"])

                        # 🔹 Estilo condicional das linhas
                        def color_row(row):
                            if "❌" in row["Mensagem"] or "⚠️" in row["Mensagem"]:
                                return ['background-color: #ff4d4d; color: white'] * len(row)
                            return [''] * len(row)

                        # 🔹 Quebra de linha automática para colunas longas
                        styled_df = df_result.style.set_properties(**{
                            'white-space': 'pre-wrap',
                            'word-wrap': 'break-word',
                        }).apply(color_row, axis=1)

                        # 🔹 Exibe a tabela expandida
                        st.write("### Resultados:")
                        st.dataframe(styled_df, use_container_width=True)

                    else:
                        st.success("☑️ Nenhuma anomalia TRB detectada.")

            except Exception as e:
                st.error(f"Erro ao puxar/verificar TRB: {e}")

        except Exception as e:
            st.error(f"❌ Erro no login ou extração de dados: {e}")
        finally:
            driver.quit()
