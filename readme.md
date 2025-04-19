# Serre Connectée - Automatisation avec Raspberry Pi et Kasa

## 1. À Propos

Ce projet fournit une application avec interface graphique (Tkinter) pour automatiser le contrôle d'une serre ou d'un environnement similaire. Il fonctionne sur Raspberry PI et nécessite un écran pour afficher l'interface ou une connexion visuelle à distance. 

Le rapsberry contrôle des prises WIFI connectées de la marque KASA/TP-LINK en combinant une logique liée à des capteurs/sensors branchés au Raspberry Pi. L'application pourrait donc servir à d'autres types d'applications similaires.

Nous avons testé l'application avec 2 capteurs de lumière I2C, 2 capteurs de température et 2 barres KASA KP-303. À priori, on pourrait connecter et contrôler de nombreuses barres de tension à partir de cette application.

Les barres KP-303 ont une capacité de 1875W et 15A selon le descriptif sur la boîte du fabricant. Il est donc important de prendre le tout en considération dans le projet. 

Par exemple, nous comptons utiliser un petit chauffage céramique <= 1500W avec sa propre barre de tension. 

La ventilation, l'arrosage et l'usage de lumières pourraient être activés par d'autres barres de tension. 

Utilise un Raspberry Pi pour :
* Lire des capteurs de température (DS18B20) et de lumière (BH1750).
* Découvrir et contrôler des multiprises intelligentes (barres de tension) Kasa/TP-Link sur le réseau local.
* Appliquer des règles définies par l'utilisateur (ex: "Allumer le chauffage si la température < 10°C") pour activer ou désactiver des appareils connectés aux prises Kasa.
* Sauvegarder la configuration (alias des appareils/capteurs, règles) dans un fichier `config.yaml`.

## 2. Matériel Requis

* **Raspberry Pi** : Modèle 3 ou plus récent recommandé pour de meilleures performances.
* **Système d'exploitation** : Une version récente de Raspberry Pi OS (anciennement Raspbian), qui inclut Python 3. (testé avec Bookworm)
* **Carte SD** : Pour le système d'exploitation du Raspberry Pi.
* **Alimentation** : Adaptée à votre modèle de Raspberry Pi. (3 amp)
* **Capteurs de Température** : Capteurs numériques **DS18B20** (protocole 1-Wire). Vous pouvez en connecter plusieurs sur le même GPIO. (attention à la capacité de la pin 3v3 en amp, une source externe 3V3 est recommandée)
* **Capteurs de Lumière** : Capteurs numériques **BH1750** (protocole I2C) 
* **Multiprises Intelligentes Kasa/TP-Link** : Le modèle **KP303** a été testé. D'autres modèles de multiprises (ou prises simples) supportés par la bibliothèque `python-kasa` devraient potentiellement fonctionner, mais nécessiteront peut-être des ajustements mineurs dans le code ou la configuration.
* **Résistance de rappel (Pull-up)** : Une résistance de 4.7kΩ pour le bus 1-Wire (DS18B20). Souvent intégrée dans les petits modules adaptateurs pour DS18B20.
* **Câblage** : Fils de connexion (type Dupont), breadboard (optionnel).
* **(Optionnel) Alimentation Externe 3.3V** : Si vous utilisez un grand nombre de capteurs.
* **(Optionnel) Multiplexeur I2C** : Si vous utilisez plus de deux capteurs BH1750.
* **(Optionnel) Des capteurs d'humidité du sol pourraient être ajoutés au projet (non testé) et devraient fonctionner avec l'application des logiques du UI

## 3. Câblage des Capteurs

**Important :** Effectuez tous les branchements **Raspberry Pi éteint**. Utilisez toujours une alimentation 3.3V pour ces capteurs, jamais 5V sans les rectifier à 3.3V car les pin GPIO ne supportent pas le 5V.

### 3.1. Capteurs de Température DS18B20 (1-Wire)

Le protocole 1-Wire permet de connecter plusieurs capteurs sur le même bus (même 3 fils).

1.  **Fil de Données (Jaune/Data)** : Connectez le fil de données de *tous* les capteurs DS18B20 ensemble et reliez-le au **GPIO 4** (Pin 7 physique) du Raspberry Pi.
2.  **Alimentation (Rouge/VCC)** : Connectez le fil VCC de *tous* les capteurs ensemble et reliez-le à une sortie **3.3V** (Pin 1 ou 17) du Raspberry Pi.
3.  **Masse (Noir/GND)** : Connectez le fil GND de *tous* les capteurs ensemble et reliez-le à une broche **Ground (GND)** (Pin 6, 9, 14, 20, etc.) du Raspberry Pi.
4.  **Résistance Pull-up** : Connectez une résistance de 4.7kΩ entre le fil de Données (GPIO 4) et l'alimentation 3.3V. *Si vous utilisez un module adaptateur pour DS18B20, cette résistance est souvent déjà incluse.*

**Note sur l'alimentation :** Si vous connectez un grand nombre de capteurs DS18B20 (plus de 3 ou 4), l'alimentation 3.3V fournie par le Raspberry Pi pourrait devenir insuffisante, entraînant des lectures instables. Dans ce cas, il est recommandé d'utiliser une **alimentation externe 3.3V** dédiée pour alimenter les capteurs (VCC et GND), tout en gardant le fil de données connecté au GPIO 4 et une masse commune avec le Raspberry Pi.

### 3.2. Capteurs de Lumière BH1750 (I2C)

Le protocole I2C utilise des adresses uniques pour chaque appareil sur le bus. Les BH1750 ont souvent des adresses fixes.

1.  **VCC** : Connectez à une sortie **3.3V** (Pin 1 ou 17) du Raspberry Pi.
2.  **GND** : Connectez à une broche **Ground (GND)** (Pin 6, 9, 14, etc.) du Raspberry Pi.
3.  **SCL (Serial Clock)** : Connectez à la broche **SCL** (GPIO 3 / Pin 5 physique) du Raspberry Pi.
4.  **SDA (Serial Data)** : Connectez à la broche **SDA** (GPIO 2 / Pin 3 physique) du Raspberry Pi.
5.  **ADDR (Address Select)** : Laisser déconnecté ou connecter à GND pour l'adresse par défaut (souvent 0x23). Connecter à 3.3V pour l'adresse alternative (souvent 0x5C). Vérifiez la documentation de votre module spécifique.

**Note sur les adresses et le nombre de capteurs :** Ce code est configuré pour rechercher les capteurs BH1750 aux adresses I2C `0x23` et `0x5C`. Comme la plupart des modules BH1750 ne permettent que ces deux adresses, vous ne pouvez connecter directement que **deux capteurs** sur le même bus I2C. Si vous avez besoin de plus de deux capteurs de lumière, vous devrez utiliser un **multiplexeur I2C** (comme le TCA9548A) qui agit comme un aiguillage, permettant de connecter plusieurs appareils avec la même adresse sur des "canaux" différents. L'intégration d'un multiplexeur nécessiterait des modifications dans le code (`light_sensor.py`).

Liste du matériel testé :

Barre Kasa KP-303
https://www.amazon.ca/dp/B083JKSSR5
BH1750 light sensors
https://www.amazon.ca/dp/B0DDCD3VZC
Capteurs de température numérique
https://www.amazon.ca/dp/B094FKQ9BS

## 4. Installation sur Raspberry Pi

Ces étapes supposent que vous partez d'une installation fraîche de Raspberry Pi OS avec accès à internet et au terminal.

### 4.1. Configuration Initiale du Raspberry Pi

1.  **Mettre à jour le système :**
    ```bash
    sudo apt update
    sudo apt full-upgrade -y
    ```
2.  **Installer les paquets nécessaires :** `git` pour cloner le projet, `python3-venv` pour créer un environnement virtuel, et `python3-tk` pour l'interface graphique.
    ```bash
    sudo apt install git python3-venv python3-tk -y
    ```
3.  **Activer les interfaces matérielles :**
    ```bash
    sudo raspi-config
    ```
    * Naviguez jusqu'à `Interface Options`.
    * Activez `I2C`.
    * Activez `1-Wire`.
    * Choisissez `<Finish>` et acceptez de redémarrer (`reboot`) lorsque demandé.

### 4.2. Installation du Logiciel de la Serre

1.  **Cloner le dépôt Git :** Ouvrez un terminal et clonez ce projet. Remplacez `URL_DU_DEPOT` par l'URL correcte si vous l'avez forkée ou si elle est différente.
    ```bash
    git clone [https://github.com/alafortu/serre.git](https://github.com/alafortu/serre.git)
    ```
    *(Si vous n'avez pas Git, installez-le avec `sudo apt install git`)*

2.  **Accéder au dossier du projet :**
    ```bash
    cd serre
    ```

3.  **Créer un environnement virtuel Python :** C'est une bonne pratique pour isoler les dépendances de ce projet.
    ```bash
    python3 -m venv serre_auto
    ```
    *(Le nom `serre_auto` est arbitraire, vous pouvez choisir un autre nom)*

4.  **Activer l'environnement virtuel :** Vous devrez faire cela **chaque fois** que vous ouvrez un nouveau terminal pour travailler sur ce projet.
    ```bash
    source serre_auto/bin/activate
    ```
    *(Votre invite de commande devrait maintenant être préfixée par `(serre_auto)`)*

5.  **Installer les dépendances Python :** `pip` lira le fichier `requirements.txt` (la version révisée) et installera les bibliothèques nécessaires *dans* l'environnement virtuel.
    ```bash
    pip install -r requirements.txt
    ```

## 5. Utilisation

1.  **Activer l'environnement virtuel** (si ce n'est pas déjà fait) :
    ```bash
    source serre_auto/bin/activate
    ```
2.  **Lancer l'application :**
    ```bash
    python greenhouse_v3.py
    ```

    L'interface graphique devrait apparaître. Vous pouvez y ajouter/modifier/supprimer des règles, voir le statut des capteurs et des prises, démarrer/arrêter le monitoring et sauvegarder la configuration.

## 6. Mises à Jour du Code

Pour récupérer les dernières modifications du code depuis le dépôt Git :

1.  **Accéder au dossier du projet :**
    ```bash
    cd serre
    ```
2.  **Activer l'environnement virtuel :**
    ```bash
    source serre_auto/bin/activate
    ```
3.  **Télécharger les mises à jour :**
    ```bash
    git pull origin main
    ```
    *(Cela suppose que vous travaillez sur la branche `main` et que l'origine est configurée correctement)*
4.  **(Optionnel) Mettre à jour les dépendances :** Si le fichier `requirements.txt` a été modifié dans la mise à jour, réinstallez les dépendances :
    ```bash
    pip install -r requirements.txt
    ```

## 7. Licence

Ce projet est distribué sous une licence open source permissive Licence MIT 

Veuillez noter que les bibliothèques tierces utilisées par ce projet (telles que `python-kasa`, `w1thermsensor`, `Adafruit-Blinka`, `PyYAML`, etc.) ont leurs propres licences distinctes. Il est de votre responsabilité de consulter et de respecter les termes de ces licences.

## 8. Remerciements

Le concept, la planification et une partie significative du code de ce projet ont été développés en collaboration avec les intelligences artificielles **Google Gemini (modèle Pro 2.5 expérimental)** et **OpenAI GPT-4o**. Leur assistance a été précieuse pour le brainstorming, la génération de code initial, le débogage et la structuration du projet.

## ⚠️ Avertissement Important / Disclaimer

Ce projet est partagé à des fins purement **éducatives et démonstratives**. Il implique la manipulation de composants électroniques et le contrôle d'appareils électriques via le réseau.

**L'utilisation de ce code et des instructions fournies se fait entièrement à vos propres risques et périls.**

L'auteur (ou les auteurs/contributeurs) de ce projet **décline toute responsabilité** en cas de :
* **Dommages matériels** : Incluant, sans s'y limiter, les bris de Raspberry Pi, de capteurs, de multiprises Kasa, d'appareils connectés ou de tout autre équipement.
* **Blessures corporelles** : Résultant de la manipulation des composants, de chocs électriques ou d'autres accidents.
* **Incendies** : Pouvant être causés par une surcharge des circuits, un câblage défectueux ou un dysfonctionnement du système.
* **Toute autre conséquence négative** : Directe ou indirecte, résultant de l'utilisation, de la modification ou de la mauvaise interprétation de ce projet.

Il est de **votre seule responsabilité** de :
* **Comprendre** le fonctionnement du code, des montages électroniques et des principes électriques de base.
* **Effectuer vos propres recherches** sur les bonnes pratiques, les normes de sécurité applicables (électricité, câblage) et les limitations techniques des composants que vous utilisez.
* **Ne jamais dépasser les capacités nominales** (courant, tension, puissance) indiquées pour vos multiprises Kasa, l'alimentation de votre Raspberry Pi, votre câblage et tout appareil contrôlé. Une surcharge peut entraîner des pannes, des dommages irréversibles et présente un risque sérieux d'incendie.
* **Faire preuve de prudence** lors du contrôle d'appareils potentiellement dangereux ou à forte consommation (chauffages, pompes, éclairage haute puissance, etc.). Une automatisation défaillante pourrait avoir des conséquences graves.

**En utilisant ce projet, vous reconnaissez avoir lu et compris cet avertissement et vous acceptez d'assumer l'entière responsabilité de votre propre installation et de ses conséquences.**






