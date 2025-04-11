APP qui détecte les barres KASA pour contrôler les prises

pour installer sur raspberry pi, ouvrir la console et suivre les étapes suivantes une ligne à la fois :

git --version # valider si tu as git
si ça sort pas une version, 
sudo apt update
sudo apt install git

Si tu as git :

git clone https://github.com/alafortu/serre.git    #copie ce repository

cd serre

sudo apt install python3-venv #install venv pour créer un environnement dédié à ce programme

python3 -m venv serre_auto # pour appeler l'environnement serre_auto

source serre_auto/bin/activate #active l'environnement

pip install requirements.txt #ça va installer les dépendances requises pour runner le code

python main.py  #va rouler l'app