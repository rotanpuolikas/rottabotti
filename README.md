# rottabotti discordiin

tällä hetkellä rottabotti pystyy vaihtaan käyttäjien nimiä ja toimiin perus musabottina.


### nimifunktiot:
  
**/nimi** input 2 asiaa, eka käyttäjä kenen nimi vaihetaan, sitte se nimi mihin se vaihetaan
  
### Musabottifunktiot:
  
**/soita** ettii youtubesta annettavan queryn, ja soittaa ensimmäisen tuloksen
  
**/lopeta** lopettaa musiikin toistamisen välittömästi
  
**/skipp** skippaa seuraavaan jonossa olevaan biisiin (kaks p-kirjainta ihan tahalleen)
  
**/jono** näyttää jonon...
  
**/liity** laittaa botin liittyyn sille kanavalle missä sää oot, lähinnä debugaustarkotuksiin

### Filtterit
  
Kaikkia näitä edeltää **/filter**, eli koko syntaksi olisi **/filterbass**

**bass** laittaa päälle ffmpeg filtterin, säätää 40 desibelin lähialueita annettavalla määrällä, range -50dB -> 50dB, default 10 jos ei muuta inputtia

**tempo** nostaa tai laskee tempoa prosentuaalisesti, ei vaikuta säveltaajuuteen. range -50% -> 100%, default 0

**pitch** nostaa tai laskee säveltaajuutta, vaikuttaa tempoon. range 0.5 -> 2, default 1

**anime** on basically nightcore filtteri, laittaa kimeän animetylleröäänen ja nopeuttaa biisiä
  
**filterpois** et varmaan koskaan arvaa mitä tää tekkee