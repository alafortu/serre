git --version # valider si tu as git
si ça sort pas une version, 
sudo apt update
sudo apt install git
git clone https://github.com/alafortu/serre.git    #copie ce repository
cd serre
sudo apt install python3-venv #install venv pour créer un environnement dédié à ce programme
python3 -m venv serre_auto # pour appeler l'environnement serre (ou autre nom à ta guise)
source serre_auto/bin/activate #active l'environnement (un environnement ça isole dans le fond ton programme dans sa propre "boite")
pip install requirements.txt #ça va installer les dépendances requises pour runner le code, ça va prendre quelques minutes sur ton raspberry pi 3
python main.py