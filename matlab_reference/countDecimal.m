function numOfPlaces = countDecimal(num)

num_char = num2str(num,'%g');
len = length(num_char);
point_idx = strfind(num_char,'.');
if ~isempty(point_idx)
    numOfPlaces = len - point_idx;
else
    numOfPlaces = 0;
end

end