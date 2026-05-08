function numOfFigs = getNumOfFigs()

h = findobj('type','figure');
numOfFigs = length(h);

end