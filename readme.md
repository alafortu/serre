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

pip install -r requirements.txt #ça va installer les dépendances requises pour runner le code

python main.py  #va rouler l'app


___________________________________________________
pour tester 1-wire temp sensor 

brancher les 2 sensors ds18b20 de la façon suivante (on met les 2 sensors en même temps - on peut en brancher plus que 2 car on utilise protocole 1 wire, une seul résistance 4.7k ohm est suffisante et déjà onboard sur ce module):
1. le fil rouge du sensor au module dans Vcc
2. le fil jaune sur le module dans Dat
3. le fil noir au module dans Gnd

Ensuite, à partir du module, 
1. connecter Gnd sur un ground du rasp pi (j'ai pris pin 14)
2. connecter dat  sur gpio 4 (pin 7) sur rasp pi
3. connecter Vcc sur 3.3v soit pin 1 ou pin 17

Comme j'ai déjà une fan branchée sur pin 1 (3.3v) et sur pin 6 (GND), j'utilise d'autres pin. 
L'important est de ne pas mettre de 5V dessus. On prend 3.3V.

Dans le terminal du raspberry pi :
sudo raspi-config

naviguer à interface options
choisir I7 1-Wire et l'enable

Tu peux aussi aller dans /boot/config.txt
et ajouter la ligne :
dtoverlay=w1-gpio

En faisant <finish> on REBOOT LE RASPBERRY À CE STADE

Une fois reboot, on ouvre un terminal :
sudo modprobe w1-gpio
sudo modprobe w1-therm

cd serre  #on va dans le folder serre

source serre_auto/bin/activate    #il faut activer l'environnement 

pip install w1thermsensor   #je vais l'ajouter au requirements.txt c'est un module qui contrôle les sensors ds18b20

find . -type d -name "__pycache__" -exec rm -r {} +  #je vais ajouter un gitignore on va pouvoir skipper ça à l'avenir

git pull origin main  #va mettre à jour le code sur le raspberry pi

cd test #j'ai mis le code de sensor dans les codes tests

python test_temp_sensor.py  #va rouler le code qui lit les températures des sensors de température 





