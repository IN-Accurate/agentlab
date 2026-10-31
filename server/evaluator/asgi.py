from app.main import create_app
from evaluator import Evaluator

app = create_app(evaluator=Evaluator())
