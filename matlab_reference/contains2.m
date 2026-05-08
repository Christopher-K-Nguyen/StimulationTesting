function hasPat = contains2(str,pat)

% Return false immediately if the string or pattern is empty
if isempty(str) || isempty(pat)
    hasPat = false;
else
    % Convert pat to a cell if it's not already one
    if ~iscell(pat)
        pat = {pat};
    end

    % Filter out non-string elements from pat
    pat_fix = pat(cellfun(@ischar,pat));

    % Perform a case-insensitive search of pat in str
    hasContains = containsi(str,pat_fix);

    % Check if any pattern was found
    hasPat = any(any(hasContains));
end

end