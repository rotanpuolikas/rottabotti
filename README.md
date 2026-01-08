# rottabotti discordiin

tällä hetkellä rottabotti pystyy vaihtaan käyttäjien nimiä ja toimiin perus musabottina.

älä pliis käytä rottabottia sun servulla, tää ei oo production ready ja sisältää todella paljon bugeja

tää sisältää tosi paljon hard coded juttuja, joten tän setuppaaminen aikoo olla melkosta tunkkausta, jos jonku takia semmoseen lähet. also paljon random audio tiedostoja, joita en githubbiin laita tekijänoikeussyistä


### nimifunktiot:
  
**/nimi** input 2 asiaa, eka käyttäjä kenen nimi vaihetaan, sitte se nimi mihin se vaihetaan
  
### Musabottifunktiot:
  
**/soita** ettii youtubesta annettavan queryn, ja soittaa ensimmäisen tuloksen, tälle voi myös antaa suoran youtube-linkin tai spotify kappale- tai soittolistalinkin. Soittaa silti youtubesta, mutta hakee metadatat spotifystä (vaatii spotify appiksen client id:n ja secretin)

**/soitanext** sama ku ylempi, mutta laittaa biisin jonossa heti seuraavaksi
  
**/lopeta** lopettaa musiikin toistamisen välittömästi, /poistu ja /bye tekee saman
  
**/skipp** skippaa seuraavaan jonossa olevaan biisiin (kaks p-kirjainta ihan tahalleen)
  
**/jono** näyttää jonon...
  
**/liity** laittaa botin liittyyn sille kanavalle missä sää oot, lähinnä debugaustarkotuksiin

**/loop** antaa mahdollisuuden joko 1. poistaa loop, 2. loopata yhtä biisiä tai 3. loopata koko jonoa

**/shuffle** sekoittaa jonon

**/leagueofhappiness** soittaa livin da vida loca:ni

**/hiljaisuus** soittaa satunnaisin intervallein valittuja äänitiedostoja, togglable

**/configchannel** adminkomento jolla määritetään kanava, johon botti laittelee viestejä

### Filtterit:
  
Kaikkia näitä edeltää **/filter**, eli koko syntaksi olisi **/filterbass**

**custombass** laittaa päälle ffmpeg filtterin, säätää 40 desibelin lähialueita annettavalla määrällä, range -50dB -> 50dB, default 10 jos ei muuta inputtia

**bass** nostaa bassotaajuuksia 10dB

**amis** nostaa bassotaajuuksia 50dB

**anime** on basically nightcore filtteri, laittaa kimeän animetylleröäänen ja nopeuttaa biisiä

**sigma** asettaa raten *0.85 originaalista, tehden biiseistä gigachad sigma versioita (ironista)
  
**pois** et varmaan koskaan arvaa mitä tää tekkee

### hassut funktiot:

**/gnome** gnomettaa targetin, ei vaadi et oot samassa voicessa targetin kaa
