function scale = setElectrodeScale(File)
%% Variables
% Electrodes
activeElectrode = File.Parameters.WorkingElectrode.Type;
counterElectrode = File.Parameters.CounterElectrode.Type;
channel_arr = [File.Data(:).ActiveChannel];
groupNum = length(channel_arr);
surfaceArea = File.Data(groupNum).SurfaceArea;

% Polarity
polarity = File.Parameters.Polarity;

% Configuration
configID = File.Parameters.Configuration.ID;
isMP = contains2(configID,'MP');
isPBP = contains2(configID,'PBP');
isBP = contains2(configID,'BP') && ~isPBP;
isPTP = contains2(configID,'PTP');
isTP = contains2(configID,'TP') && ~isPTP;
isPartial = isPBP || isPTP;
isCG = contains2(configID,'CG');

%% Function

switch upper(activeElectrode)
    case 'AIROF'
        switch upper(counterElectrode)
            case {'PT','PTIR'}
                switch polarity
                    case -1
                        scale = 2;
                    case 1
                        scale = 2.5;
                        if isCG
                            scale = scale * 4;
                        end
                end
            case {'AIROF','IR','AU'}
                switch polarity
                    case -1
                        if isMP
                            scale = 2.5;
                        else
                            scale = 2;
                        end
                    case 1
                        if isMP
                            scale = 10;
                        else
                            scale = 5;
                        end
                end
            case {'SS'}
                switch polarity
                    case -1
                        scale = 1.5;
                    case 1
                        scale = 3.5;
                end
            case 'TI'
                switch polarity
                    case -1
                        scale = 1;
                    case 1
                        scale = 2;
                end
            case 'W'
                switch polarity
                    case -1
                        scale = 0.05;
                    case 1
                        scale = 0.1;
                end
        end
        
        if isBP
            scale = scale * 3;
        elseif isTP
            scale = scale * 4;
        end
        if isPartial || isCG
            scale = scale * 5;
        end
    case 'SIROF'
        switch upper(counterElectrode)
            case {'PT','PTIR','AU'}
                switch polarity
                    case -1
                        scale = 6;
                    case 1
                        scale = 7;
                end
            case {'SIROF','IR'}
                switch polarity
                    case -1
                        scale = 1;
                    case 1
                        scale = 1.25;
                end
            case {'SS','TI'}
                switch polarity
                    case -1
                        scale = 1.5;
                    case 1
                        scale = 1;
                end
            case 'W'
                switch polarity
                    case -1
                        scale = 0.75;
                    case 1
                        scale = 2.5;
                end
        end
        if isCG
            scale = scale * 2.5;
        end
        if isBP || isPBP
            scale = scale * 3;
        elseif isTP || isPTP
            scale = scale * 4;
        end
        if isPartial
            scale = scale * 1.5;
        end
    case 'TIN'
    case 'PEDOT'
        switch polarity
            case -1
                scale = 20;
            case 1
                scale = 5;
        end
    case 'PTIR'
    case 'PT'
end



end