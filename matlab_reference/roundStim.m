function newAmplitude_uA = roundStim(amplitude_uA,varargin)

numOfVar = length(varargin);
if numOfVar > 0
    precision = varargin{1};
    if isnumeric(precision)
        if precision < 0.03
            precision = 0.03;
        end
    else
        if contains2(precision,'tenth')
            precision = 0.1;
        elseif contains2(precision,'one')
            precision = 1;
        end
    end
else
    precision = 0.05;
end
places = -floor(log10(precision));
amplitude_uA_fix = round(amplitude_uA ./ precision) .* precision;
newAmplitude_uA = round(amplitude_uA_fix,places);

end