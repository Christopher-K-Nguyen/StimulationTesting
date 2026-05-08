function num_new = convert(num,oldScale,newScale)

num_char = num2str(num);
numOfDigits = length(num_char);
if contains(num_char,'.')
    numOfDigits = numOfDigits - 1;
end

if isnumeric(oldScale)
    num_fix = num * 10^oldScale;
elseif ischar(oldScale)
    switch oldScale
        case {'T','tera','TERA'}
            oldScale_use = 12;
        case {'G','giga','GIGA','gig','GIG'}
            oldScale_use = 9;
        case {'M','mega','MEGA','meg','MEG'}
            oldScale_use = 6;
        case {'k','kilo','KILO'}
            oldScale_use = 3;
        case {'N','base','BASE'}
            oldScale_use = 0;  
        case {'c','centi','CENTI','cent','CENT'}
            oldScale_use = -2;
        case {'m','milli','MILLI','mill','MILL','mil','MIL'}
            oldScale_use = -3;
        case {'u','mu','MU','micro','MICRO'}
            oldScale_use = -6;
        case {'n','nano','NANO'}
            oldScale_use = -9;
        case {'p','pico','PICO'}
            oldScale_use = -12;
        case {'f','fempto','FEMPTO'}
            oldScale_use = -15;
    end
    num_fix = num * 10^oldScale_use;
else
    num_new = [];
    return;
end

if isnumeric(newScale)
    factor = 10^newScale;
elseif ischar(newScale)
    switch newScale
        case {'T','tera','TERA'}
            newScale_use = 12;
        case {'G','giga','GIGA','gig','GIG'}
            newScale_use = 9;
        case {'M','mega','MEGA','meg','MEG'}
            newScale_use = 6;
        case {'k','kilo','KILO'}
            newScale_use = 3;
        case {'N','base','BASE'}
            newScale_use = 0;
        case {'c','centi','CENTI','cent','CENT'}
            newScale_use = -2;
        case {'m','milli','MILLI','mill','MILL','mil','MIL'}
            newScale_use = -3;
        case {'u','mu','MU','micro','MICRO'}
            newScale_use = -6;
        case {'n','nano','NANO'}
            newScale_use = -9;
        case {'p','pico','PICO'}
            newScale_use = -12;
        case {'f','fempto','FEMPTO'}
            newScale_use = -15;
    end
    factor = 10^newScale_use;
else
    num_new = [];
    return;
end

num_calc = num_fix / factor;
num_new = round(num_calc,numOfDigits,'significant');

end