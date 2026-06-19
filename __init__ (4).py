NAME_FALLBACK = {
    "AAPL":"Apple", "MSFT":"Microsoft", "NVDA":"NVIDIA", "AMZN":"Amazon", "GOOGL":"Alphabet Class A",
    "GOOG":"Alphabet Class C", "META":"Meta Platforms", "TSLA":"Tesla", "AVGO":"Broadcom", "AMD":"Advanced Micro Devices",
    "MU":"Micron Technology", "NFLX":"Netflix", "ORCL":"Oracle", "ARM":"Arm Holdings", "INTC":"Intel", "QCOM":"QUALCOMM",
    "CSCO":"Cisco Systems", "DELL":"Dell Technologies", "PLTR":"Palantir", "SMCI":"Super Micro Computer", "CRM":"Salesforce",
    "ADBE":"Adobe", "AMAT":"Applied Materials", "LRCX":"Lam Research", "KLAC":"KLA", "TXN":"Texas Instruments",
    "LLY":"Eli Lilly", "NVO":"Novo Nordisk", "VRTX":"Vertex", "REGN":"Regeneron", "MRNA":"Moderna", "PFE":"Pfizer",
    "JNJ":"Johnson & Johnson", "UNH":"UnitedHealth", "XBI":"SPDR S&P Biotech ETF", "BOTZ":"Global X Robotics & AI ETF",
    "ROBO":"ROBO Global Robotics & Automation ETF", "SPY":"SPDR S&P 500 ETF", "QQQ":"Invesco QQQ ETF", "SMH":"VanEck Semiconductor ETF",
    "XLK":"Technology Select Sector SPDR", "XLF":"Financial Select Sector SPDR", "XLE":"Energy Select Sector SPDR",
    "SPACEX":"SpaceX · 비상장 참고"
}

UNIVERSE = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","GOOG","META","TSLA","AVGO","AMD","NFLX","ORCL","COST","PLTR","ADBE",
    "CRM","CSCO","INTC","MU","QCOM","AMAT","LRCX","KLAC","TXN","ARM","SMCI","PANW","CRWD","NOW","SHOP",
    "UBER","ABNB","MELI","PYPL","SBUX","PEP","TMUS","CMCSA","INTU","ADP","ISRG","VRTX","REGN",
    "LLY","NVO","MRNA","PFE","JNJ","UNH","TMO","DHR","ABT","ABBV","GILD","AMGN","BIIB","SNOW",
    "DDOG","NET","MDB","ZS","DELL","HPQ","WMT","HD","LOW","JPM","BAC","GS","MS","V","MA","AXP","XOM","CVX",
    "COP","SLB","GE","CAT","DE","HON","RTX","LMT","NOC","ROK","SYM","TER"
]

SECTOR_ETFS = {
    "S&P500":"SPY", "NASDAQ":"QQQ", "반도체":"SMH", "기술":"XLK", "AI·소프트웨어":"IGV",
    "바이오·제약·의료":"XBI", "헬스케어":"XLV", "로봇":"BOTZ", "금융":"XLF", "에너지":"XLE",
    "산업재":"XLI", "소비재":"XLY", "필수소비":"XLP"
}

DEFAULT_HOLDINGS = [
    {"actual": True, "ticker": "MU", "name": "Micron Technology", "buy_price": 0.0, "quantity": 0.0, "buy_date": "", "memo": ""},
    {"actual": True, "ticker": "NVDA", "name": "NVIDIA", "buy_price": 0.0, "quantity": 0.0, "buy_date": "", "memo": ""},
    {"actual": True, "ticker": "AMD", "name": "Advanced Micro Devices", "buy_price": 0.0, "quantity": 0.0, "buy_date": "", "memo": ""},
    {"actual": True, "ticker": "TSLA", "name": "Tesla", "buy_price": 0.0, "quantity": 0.0, "buy_date": "", "memo": ""},
    {"actual": True, "ticker": "ORCL", "name": "Oracle", "buy_price": 0.0, "quantity": 0.0, "buy_date": "", "memo": ""},
    {"actual": True, "ticker": "SPACEX", "name": "SpaceX · 비상장 참고", "buy_price": 0.0, "quantity": 0.0, "buy_date": "", "memo": "비상장 수동관리"},
]
