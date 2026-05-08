function [filesList,numOfFiles] = getAllFiles(folderPath,ext)

ext = erase(lower(ext),'.');
ext_use = sprintf('*.%s',ext);
filesInFolder = fullfile(folderPath,ext_use);    % files in folder
filesList = dir(filesInFolder);               % add files to directory
numOfFiles = length(filesList);               % number of .dta files

end