function [File,isQuit] = getWorkingElectrode(File,varargin)
%% Constants
LISTDLG_SIZE = [180 100];
BUTTON_CONFIRM = 'Confirm';
BUTTON_TRY = 'Try Again';
BUTTON_QUIT = 'Quit';
opts.Default = BUTTON_CONFIRM;
opts.Interpreter = 'tex';

%% Variables
deviceType = File.Parameters.Device;
type = [];
numOfVar = length(varargin);
if numOfVar > 1
    type = varargin{1};
    hasWindow = false;
    if ~isempty(type)
        var2 = varargin{2};
        var2_len = length(var2);
        if ismatrix(var2) && var2_len > 1
            waterWindow = var2;
            hasWindow = true;
        else
            limit1 = varargin{3};
            limit2 = varargin{4};
            if limit1 < limit2
                lowerPotential = limit1;
                upperPotential = limit2;
            else
                lowerPotential = limit2;
                upperPotential = limit1;
            end
            waterWindow = [lowerPotential upperPotential];
        end
    end
end
isQuit = false;
electrode = '';

% Reference
switch deviceType
    case {'UTD_MEA','UTD_Test'}
        work_cell = { ...
            'SIROF', ...
            'RuOx', ...
            'PEDOT'};
        waterWindow_mat = [ ...
            -0.6 0.8;...
            -0.5 0.5;...
            -0.5 0.5];
    case {'Blackrock_Omnetics','Blackrock_PCB'}
        work_cell = { ...
            'SIROF', ...
            'Pt'};
        waterWindow_mat = [ ...
            -0.6 0.8;...
            -0.6 0.8];
    case 'MicroProbes'
        work_cell = { ...
            'AIROF', ...
            'Ir', ...
            'PtIr'};
        waterWindow_mat = [ ...
            -0.6 0.8;...
            -0.6 0.8;...
            -0.6 0.8;...
            -0.6 0.8];
    case 'NeuroNexus'
        work_cell = { ...
            'AIROF', ...
            'Ir', ...
            'PtIr', ...
            'Pt'};
        waterWindow_mat = [ ...
            -0.6 0.8;...
            -0.6 0.8;...
            -0.6 0.8;...
            -0.6 0.8];
    case 'Other'
        work_cell = { ...
            'IrOx', ...
            'RuOx', ...
            'PEDOT', ...
            'Ir', ...
            'PtIr', ...
            'Pt'};
        waterWindow_mat = [ ...
            -0.6 0.8;...
            -0.5 0.5;...
            -0.5 0.5;...
            -0.6 0.8;...
            -0.6 0.8;...
            -0.6 0.8];
end


WorkingElectrode = struct( ...
    'Type',[], ...
    'LowerPotential',[], ...
    'UpperPotential',[]);

%% Function
if isempty(type)
    fprintf('Select working electrode...');

    confirmPotential = false;
    while ~confirmPotential
        % Initial Reference
        [workingList_idx,~] = listdlg( ...
            'PromptString','Select working electrode', ...
            'ListString',work_cell, ...
            'SelectionMode','single', ...
            'InitialValue',1, ...
            'ListSize',LISTDLG_SIZE);
        if ~isempty(workingList_idx)
            electrode = work_cell{workingList_idx};
            waterWindow = waterWindow_mat(workingList_idx,:);
        else
            isQuit = true;
            break;
        end

        % Potential
        lowerPotential = waterWindow(1);
        upperPotential = waterWindow(2);
        questInput = sprintf( ...
            'Working Electrode: {\\bf%s (%.2f to %.2f V)}', ...
            electrode, ...
            lowerPotential,upperPotential);
        questTitle = 'Confirm Working Electrode';
        questPotential = questdlg( ...
            questInput, ...
            questTitle, ...
            BUTTON_CONFIRM,BUTTON_TRY,BUTTON_QUIT, ...
            opts);
        switch questPotential
            case BUTTON_CONFIRM
                confirmPotential = true;
                fprintf('%s\n',electrode);
            case BUTTON_TRY
                continue;
            case BUTTON_QUIT
                isQuit = true;
                break;
        end
    end
    if isQuit
        return;
    end
else
    switch upper(type)
        case {'IRIDIUM OXIDE','IROX','SIROF','AIROF', ...
                'EIROF','TIROF','IRO','IRIDIUM DIOXIDE'}
            type = 'IrOx';
        case {'RUTHENIUM OXIDE','RUOX','RUO','RU'}
            type = 'RuOx';
        case {'PEDOT','POLY(3,4-ETHYLENEDIOXYTHIOPHENE)'}
            type = 'PEDOT';
        case {'PLATINUM-IRIDIUM','PLATINUM IRIDIUM','PLATINUMIRIDIUM','PTIR'}
            type = 'PtIr';
        case {'IRIDIUM','IR'}
            type = 'Ir';
        case {'PLATINUM','PLAT','PT'}
            type = 'Pt';
    end
    if ~hasWindow
        electrode_idx = find(containsi(work_cell,type));
        electrode = work_cell{electrode_idx};
        waterWindow = waterWindow_mat(electrode_idx,:);
    end
    lowerPotential = waterWindow(1);
    upperPotential = waterWindow(2);
end

%% Store
WorkingElectrode.Type = electrode;
WorkingElectrode.LowerPotential = lowerPotential;
WorkingElectrode.UpperPotential = upperPotential;
File.Parameters.WorkingElectrode = WorkingElectrode;

end